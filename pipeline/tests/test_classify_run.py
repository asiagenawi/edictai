"""`classify run`, `eval-gate`, `refresh`, and `summary`, with the Anthropic client faked."""

import io
import json
import shutil
import zipfile
from datetime import date
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from statescope.classify.commands import cmd_check
from statescope.classify.common import ClassifyPaths, read_json, read_jsonl, write_jsonl
from statescope.classify.evalgate import eval_gate, gate_path
from statescope.classify.judgments import check_full
from statescope.classify.llm import Budget, Judge, Request, api_schema, cost_of
from statescope.classify.refresh import append_to_list, new_actions, refresh
from statescope.classify.run import run_workdir
from statescope.classify.settings import Settings, load_settings
from statescope.classify.summary import build_summary, markdown
from statescope.classify.texts import TextStore
from statescope.cli import parser
from statescope.paths import Paths
from statescope.validate import validate
from statescope.vocab import Vocab
from statescope.yamlio import load_yaml

from .conftest import REPO, make_tree

TODAY = date(2026, 10, 17)
PRICES = {
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.2}
}
TEXT = (
    "AN ACT relating to artificial intelligence in public schools.\n"
    "SECTION 1. Section 38.0231, Education Code, is added.\n"
    "(a) Each school district shall adopt a policy on the use of artificial intelligence "
    "by students.\n"
)
QUOTE = (
    "Each school district shall adopt a policy on the use of artificial intelligence by students."
)


def settings(**over):
    base = dict(
        prices=PRICES,
        batch_poll_seconds=0,
        direct_workers=1,
        items_per_request={"triage": 2, "full": 1},
        max_tokens={"triage": 1000, "full": 1000},
        spend_cap_usd=100.0,
    )
    base.update(over)
    return Settings(**base)


def candidate(n, title="Relating to artificial intelligence in public schools", **over):
    row = {
        "bill_id": 9000 + n,
        "state": "TX",
        "jurisdiction": "us-tx",
        "session": "2025",
        "legiscan_session_id": 2160,
        "bill_number": f"HB{n}",
        "identifier": f"HB {n}",
        "bill_type": "B",
        "title": title,
        "description": title,
        "status_text": "Introduced",
        "status_date": f"2025-02-{n:02d}",
        "history": [{"date": f"2025-02-{n:02d}", "action": "Filed", "chamber": "H"}],
        "sponsors": [{"name": "Jane Doe", "party": "R", "role": "Rep", "sponsor_type_id": 1}],
        "texts": [
            {
                "doc_id": 500 + n,
                "type": "Introduced",
                "date": f"2025-02-{n:02d}",
                "mime": "text/html",
                "state_link": f"https://capitol.texas.gov/tlodocs/89R/billtext/html/HB{n:05d}I.htm",
            }
        ],
        "url": f"https://capitol.texas.gov/BillLookup/History.aspx?LegSess=89R&Bill=HB{n}",
        "matched_by": "title",
        "matched_keywords": ["artificial intelligence"],
        "trigger_strength": "strong",
        "existing_record": None,
    }
    row.update(over)
    return row


def key(n):
    return f"us-tx-2025-hb-{n}"


@pytest.fixture
def repo(tmp_path):
    paths = make_tree(tmp_path / "repo")
    (paths.root / "docs").mkdir()
    shutil.copy(REPO / "docs" / "methodology.md", paths.root / "docs" / "methodology.md")
    return paths


def write_candidates(paths, rows, name="2026-10-17.jsonl"):
    write_jsonl(paths.cache / "candidates" / name, rows)


def store_texts(paths, numbers):
    store = TextStore(ClassifyPaths(paths).texts)
    for n in numbers:
        store.store(
            key(n),
            {
                "doc_id": 500 + n,
                "key": key(n),
                "status": "ok",
                "type": "Introduced",
                "state_link": f"https://capitol.texas.gov/tlodocs/89R/billtext/html/HB{n:05d}I.htm",
            },
            TEXT,
        )
    store.save_index()


# -- a scripted client ------------------------------------------------------------------


def triage_row(k, attempt):
    return {"key": k, "decision": "in", "reason": "Requires district AI policies."}


def full_row(k, attempt):
    return {
        "key": k,
        "in_scope": True,
        "confidence": "high",
        "education_levels": ["k12"],
        "topics": ["local-policy-requirement"],
        "summary": "Requires each school district to adopt a policy on student use of AI.",
        "provisions": [
            {
                "type": "requires-local-ai-policy",
                "description": "Each district adopts a policy on student AI use.",
                "locator": "Sec. 1, Education Code 38.0231(a)",
                "quote": QUOTE,
            }
        ],
        "reasons": "Sec. 1 requires each district to adopt a student AI-use policy.",
    }


def message(data, usage=(1000, 200), stop="end_turn"):
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="text", text=json.dumps(data))],
        usage=SimpleNamespace(
            input_tokens=usage[0],
            output_tokens=usage[1],
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


def items_of(params):
    text = params["messages"][0]["content"]
    body = text.split("one JSON object per line):\n\n", 1)[1].split("\n\nYour previous", 1)[0]
    return [json.loads(line) for line in body.strip().splitlines()]


class FakeBatches:
    def __init__(self, client):
        self.client = client
        self.created = []
        self.cancelled = []
        self.fail_create = False
        self.never_end = False
        self.drop_results = 0  # results to leave out of an ended batch

    def create(self, requests):
        if self.fail_create:
            raise anthropic.APIConnectionError(
                request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages/batches")
            )
        bid = f"msgbatch_{len(self.created) + 1}"
        self.created.append((bid, list(requests)))
        return SimpleNamespace(id=bid, processing_status="in_progress")

    def retrieve(self, bid):
        if self.never_end and bid not in self.cancelled:
            return SimpleNamespace(id=bid, processing_status="in_progress")
        return SimpleNamespace(id=bid, processing_status="ended")

    def cancel(self, bid):
        self.cancelled.append(bid)

    def results(self, bid):
        requests = dict(self.created)[bid]
        if bid in self.cancelled:
            # Cancelled before anything ran.
            return [
                SimpleNamespace(custom_id=r["custom_id"], result=SimpleNamespace(type="canceled"))
                for r in requests
            ]
        out = []
        for r in requests[self.drop_results :]:
            out.append(
                SimpleNamespace(
                    custom_id=r["custom_id"],
                    result=SimpleNamespace(
                        type="succeeded", message=self.client.respond(r["params"])
                    ),
                )
            )
        return out


class FakeClient:
    def __init__(self, triage=triage_row, full=full_row):
        self.policy = {"triage": triage, "full": full}
        self.attempts = {}
        self.direct = []
        self.usage = (1000, 200)
        self.messages = SimpleNamespace(create=self.create, batches=FakeBatches(self))

    def respond(self, params):
        stage = "triage" if params["system"][0]["text"].startswith("# Triage") else "full"
        rows = []
        for item in items_of(params):
            k = item["key"]
            n = self.attempts[(stage, k)] = self.attempts.get((stage, k), 0) + 1
            row = self.policy[stage](k, n)
            if row is not None:
                rows.append(row)
        return message({"judgments": rows}, self.usage)

    def create(self, **params):
        self.direct.append(params)
        return self.respond(params)

    @property
    def calls(self):
        return len(self.direct) + sum(len(r) for _, r in self.messages.batches.created)


def judge(client, **over):
    s = settings(**over)
    return Judge(s, Budget(s.spend_cap_usd), client, sleep=lambda _s: None), s


# -- schema and cost -----------------------------------------------------------------


def test_api_schema_strips_what_the_grammar_does_not_take(repo):
    from statescope.classify.instructions import full_schema

    schema = api_schema(full_schema(Vocab.load(repo.vocab)))
    line = schema["properties"]["judgments"]["items"]
    text = json.dumps(schema)
    for word in ("minLength", "maxLength", "uniqueItems", '"if"', '"then"', "$schema"):
        assert word not in text
    assert line["properties"]["confidence"] == {
        "enum": ["high", "medium", "low"],
        "type": "string",
    }
    assert line["additionalProperties"] is False and schema["required"] == ["judgments"]


def test_cost_and_settings(repo, monkeypatch):
    s = settings()
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_input_tokens": 0}
    assert cost_of(usage, s.price()) == pytest.approx(3.0)
    assert cost_of(usage, s.price(), 0.5) == pytest.approx(1.5)
    loaded = load_settings(Paths(REPO), env={"WEEKLY_SPEND_CAP_USD": "3.5"})
    assert loaded.spend_cap_usd == 3.5 and loaded.model == "claude-sonnet-5-5"
    assert load_settings(Paths(REPO), env={}).spend_cap_usd == 10
    with pytest.raises(ValueError, match="no price"):
        Settings(model="claude-unknown").price()


# -- the run -------------------------------------------------------------------------


def test_run_batch_path_writes_checkable_outputs(repo, capsys):
    write_candidates(repo, [candidate(1), candidate(2), candidate(3)])
    store_texts(repo, [1, 2, 3])
    client = FakeClient()
    j, s = judge(client)
    cp = ClassifyPaths(repo)
    res = run_workdir(cp, j, s, today=TODAY, env={}, fetch=False)
    assert len(res.triage.judged) == 3 and len(res.full.judged) == 3
    assert client.direct == [] and len(client.messages.batches.created) == 2
    sent = client.messages.batches.created[0][1][0]["params"]
    assert sent["system"][0]["text"] == cp.instructions("triage").read_text()
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert sent["output_config"]["effort"] == "medium"
    # `classify check` and `classify import` accept the outputs unchanged.
    for out in [*res.triage.outputs, *res.full.outputs]:
        args = parser().parse_args(["--root", str(repo.root), "classify", "check", str(out)])
        assert cmd_check(args) == 0
    args = parser().parse_args(
        ["--root", str(repo.root), "classify", "import", "--strict-quotes"]
        + [str(p) for p in res.full.outputs]
    )
    assert args.func(args) == 0
    capsys.readouterr()
    rec = load_yaml(repo.data / "legislation/us-tx/2025/hb-1.yaml")
    assert rec["verification"]["status"] == "machine-verified"
    report, _ = validate(Paths(repo.root), strict=True)
    assert [i for i in report.issues if "hb-1.yaml" in i.file] == []
    assert res.judgments()[0]["url"].startswith("https://capitol.texas.gov/")


def test_run_is_idempotent(repo):
    write_candidates(repo, [candidate(1), candidate(2)])
    store_texts(repo, [1, 2])
    client = FakeClient()
    j, s = judge(client)
    cp = ClassifyPaths(repo)
    run_workdir(cp, j, s, today=TODAY, env={}, fetch=False)
    calls = client.calls
    again = run_workdir(cp, j, s, today=TODAY, env={}, fetch=False)
    assert client.calls == calls
    assert again.triage.judged == [] and again.full.judged == []
    # Earlier outputs stay listed for import, so an unmerged week is imported again.
    assert again.full.outputs == [] and len(again.import_files) == 1
    # A new candidate is the only thing judged next time.
    write_candidates(repo, [candidate(1), candidate(2), candidate(3)], "2026-10-24.jsonl")
    store_texts(repo, [3])
    third = run_workdir(cp, j, s, today=TODAY, env={}, fetch=False)
    assert [r["key"] for r in third.triage.judged] == [key(3)]


def test_run_direct_path(repo):
    write_candidates(repo, [candidate(1)])
    store_texts(repo, [1])
    client = FakeClient()
    j, s = judge(client, use_batches=False)
    res = run_workdir(ClassifyPaths(repo), j, s, today=TODAY, env={}, fetch=False)
    assert len(client.direct) == 2 and client.messages.batches.created == []
    assert len(res.full.judged) == 1
    assert j.budget.spent == pytest.approx(2 * (1000 * 2 + 200 * 10) / 1e6)


def test_batch_create_failure_falls_back_to_direct(repo):
    write_candidates(repo, [candidate(1)])
    store_texts(repo, [1])
    client = FakeClient()
    client.messages.batches.fail_create = True
    j, s = judge(client)
    res = run_workdir(ClassifyPaths(repo), j, s, today=TODAY, env={}, fetch=False)
    assert len(res.full.judged) == 1 and len(client.direct) == 2


def test_batch_timeout_cancels_and_sends_directly(repo):
    client = FakeClient()
    client.messages.batches.never_end = True
    ticks = iter(range(0, 10**6, 60))
    s = settings(batch_timeout_minutes=1)
    j = Judge(s, Budget(10), client, sleep=lambda _s: None, clock=lambda: next(ticks))
    req = Request("t-1", "# Triage instructions", _user([{"key": key(1)}]), {}, 100)
    replies = j.run([req])
    assert client.messages.batches.cancelled == ["msgbatch_1"]
    assert replies["t-1"].via == "direct" and replies["t-1"].data["judgments"][0]["key"] == key(1)


def test_batch_results_missing_are_sent_directly():
    client = FakeClient()
    client.messages.batches.drop_results = 1
    j, _ = judge(client)
    reqs = [Request(f"t-{i}", "# Triage", _user([{"key": key(i)}]), {}, 100) for i in (1, 2)]
    replies = j.run(reqs)
    assert replies["t-1"].via == "direct" and replies["t-2"].via == "batch"


def _user(items):
    from statescope.classify.run import PROMPT

    return PROMPT.format(count=len(items), items="\n".join(json.dumps(i) for i in items))


def test_budget_stops_before_the_cap(repo):
    from statescope.classify.instructions import write_instructions

    write_candidates(repo, [candidate(n) for n in range(1, 7)])
    store_texts(repo, range(1, 7))
    client = FakeClient()
    j, s = judge(client)
    cp = ClassifyPaths(repo)
    write_instructions(repo, cp.root)
    system = cp.instructions("triage").read_text()
    # Close to the worst case: about four characters a token, most of max_tokens used.
    client.usage = (len(system) // 4, 900)
    per = j.worst_case(Request("x", system, "x" * 1500, {}, 1000), s.batch_discount)
    # The first wave covers two triage requests; what they cost leaves too little for a third.
    j.budget = Budget(per * 2.2)
    res = run_workdir(cp, j, s, today=TODAY, env={}, fetch=False)
    assert j.budget.spent + j.budget.reserved <= j.budget.cap
    assert j.budget.stopped
    assert res.triage.not_sent
    judged = {r["key"] for r in res.triage.judged}
    # Keys not sent are out of their batches, so the next run batches them again.
    batched = {
        r["key"] for p in cp.stage_dir("triage").glob("batch-*.jsonl") for r in read_jsonl(p)
    }
    assert batched == judged and not (set(res.triage.not_sent) & batched)
    j.budget = Budget(100)
    later = run_workdir(cp, j, s, today=TODAY, env={}, fetch=False)
    assert {r["key"] for r in later.triage.judged} == set(res.triage.not_sent)


def test_retry_once_then_drop(repo):
    write_candidates(repo, [candidate(1), candidate(2)])
    store_texts(repo, [1, 2])
    long_reason = " ".join(["word"] * 30)

    def triage(k, attempt):
        if k == key(1) and attempt == 1:
            return {"key": k, "decision": "in", "reason": long_reason}
        return triage_row(k, attempt)

    def full(k, attempt):
        row = full_row(k, attempt)
        if k == key(2):
            row["provisions"][0]["quote"] = "Each district must adopt a strict AI policy at once."
        return row

    client = FakeClient(triage, full)
    j, s = judge(client)
    cp = ClassifyPaths(repo)
    res = run_workdir(cp, j, s, today=TODAY, env={}, fetch=False)
    assert res.triage.retried == 1 and len(res.triage.judged) == 2
    assert client.attempts[("triage", key(1))] == 2
    assert [d["key"] for d in res.full.dropped] == [key(2)]
    assert client.attempts[("full", key(2))] == 2
    assert "quote not found" in res.full.dropped[0]["errors"][0]
    assert read_json(cp.root / "dropped.json")[key(2)]["stage"] == "full"
    # The dropped key is not in a batch or an output, and is not asked again.
    out = [r["key"] for p in cp.out_dir("full").glob("*.jsonl") for r in read_jsonl(p)]
    assert out == [key(1)]
    calls = client.calls
    run_workdir(cp, j, s, today=TODAY, env={}, fetch=False)
    assert client.calls == calls
    retried = run_workdir(cp, j, s, today=TODAY, env={}, fetch=False, retry_dropped=True)
    assert [d["key"] for d in retried.full.dropped] == [key(2)]


def test_baseline_keys_are_skipped(repo):
    from statescope.classify.run import unjudged, write_baseline

    write_candidates(repo, [candidate(1), candidate(2)])
    cp = ClassifyPaths(repo)
    from statescope.classify.common import load_candidates

    write_baseline(cp, [key(1)])
    assert unjudged(cp, load_candidates(repo.cache / "candidates" / "2026-10-17.jsonl")) == [key(2)]


# -- eval gate --------------------------------------------------------------------------


def test_eval_gate_runs_only_when_the_hash_changes(repo):
    (repo.root / "pipeline" / "eval").mkdir(parents=True)
    entries = [
        {
            "id": key(1),
            "jurisdiction": "us-tx",
            "session": "2025",
            "identifier": "HB 1",
            "label": "in_scope",
            "difficulty": "clear",
        },
        {
            "id": key(2),
            "jurisdiction": "us-tx",
            "session": "2025",
            "identifier": "HB 2",
            "label": "out_of_scope",
            "difficulty": "clear",
        },
    ]
    (repo.root / "pipeline" / "eval" / "classifier_eval.yaml").write_text(json.dumps(entries))
    # Existing records are judged too: the evaluation does not skip them.
    write_candidates(repo, [candidate(1, existing_record=key(1)), candidate(2)])
    store_texts(repo, [1, 2])

    def triage(k, attempt):
        return {"key": k, "decision": "in" if k == key(1) else "out", "reason": "Metadata."}

    client = FakeClient(triage)
    s = settings()

    def factory():
        return Judge(s, Budget(10), client, sleep=lambda _s: None)

    first = eval_gate(repo, s, factory, today=TODAY)
    assert first.ran and first.passed and first.record["precision"] == 1.0
    assert first.record["matched"] == 2
    calls = client.calls
    second = eval_gate(repo, s, factory, today=TODAY)
    assert not second.ran and second.passed and client.calls == calls
    # A different effort is a different classifier.
    s2 = s.with_overrides(effort="low")
    third = eval_gate(repo, s2, lambda: Judge(s2, Budget(10), client), today=TODAY)
    assert third.ran and third.hash != first.hash
    assert len(read_json(gate_path(repo))["results"]) == 2
    # Floors are applied to the stored result.
    strict = s.with_overrides(precision_floor=1.1)
    fourth = eval_gate(repo, strict, factory, today=TODAY)
    assert not fourth.ran and not fourth.passed and "precision" in fourth.reasons[0]


def test_eval_gate_fails_when_the_classifier_misses(repo):
    (repo.root / "pipeline" / "eval").mkdir(parents=True)
    entries = [
        {
            "id": key(1),
            "jurisdiction": "us-tx",
            "session": "2025",
            "identifier": "HB 1",
            "label": "in_scope",
            "difficulty": "clear",
        }
    ]
    (repo.root / "pipeline" / "eval" / "classifier_eval.yaml").write_text(json.dumps(entries))
    write_candidates(repo, [candidate(1)])

    def triage(k, attempt):
        return {"key": k, "decision": "out", "reason": "Metadata."}

    client = FakeClient(triage)
    s = settings()
    result = eval_gate(repo, s, lambda: Judge(s, Budget(10), client), today=TODAY)
    assert not result.passed and result.record["recall"] == 0.0


# -- status refresh -----------------------------------------------------------------------


def _dataset(repo, bill):
    cache = repo.cache / "legiscan"
    (cache / "datasets").mkdir(parents=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("TX/2025/bill/HB1481.json", json.dumps({"bill": bill}))
    (cache / "datasets" / "2160.zip").write_bytes(buf.getvalue())
    (cache / "datasets" / "2160.json").write_text(json.dumps({"dataset_hash": "abc"}))


def test_refresh_appends_new_legiscan_actions_in_place(repo):
    path = repo.data / "legislation/us-tx/2025/hb-1481.yaml"
    before = path.read_text()
    history = [
        {"date": "2025-02-10", "action": "Filed", "chamber": "H"},
        {"date": "2025-03-01", "action": "Referred to Public Education", "chamber": "H"},
        {"date": "2025-04-15", "action": "Passed", "chamber": "H"},
        {"date": "2025-05-10", "action": "Passed", "chamber": "S"},
        {"date": "2025-06-20", "action": "Signed by the Governor", "chamber": "H"},
        {"date": "2025-06-21", "action": "Effective on 9/1/25", "chamber": "H"},
        {"date": "2027-01-01", "action": "Future", "chamber": "H"},
    ]
    _dataset(repo, {"bill_id": 123456, "history": history, "texts": []})
    result = refresh(repo, today=TODAY, sources=["legiscan"])
    assert [u.id for u in result.updates] == ["us-tx-2025-hb-1481"]
    after = path.read_text()
    # The new action is inserted at the end of the actions list; no other line changes.
    head, tail = before.split("versions:\n", 1)
    assert after.startswith(head) and after.endswith("versions:\n" + tail)
    inserted = after[len(head) : len(after) - len("versions:\n" + tail)]
    assert inserted.startswith('  - date: "2025-06-21"\n    description: Effective on 9/1/25\n')
    rec = load_yaml(path)
    assert rec["actions"][-1]["description"] == "Effective on 9/1/25"
    assert len(rec["actions"]) == 6
    assert rec["versions"] == load_yaml_text(before)["versions"]
    report, _ = validate(Paths(repo.root), strict=True, today=TODAY)
    assert [i for i in report.issues if "hb-1481" in i.file] == []
    # Nothing new the second time.
    assert refresh(repo, today=TODAY, sources=["legiscan"]).updates == []
    assert path.read_text() == after


def load_yaml_text(text):
    import yaml

    from statescope.yamlio import RecordLoader

    return yaml.load(text, Loader=RecordLoader)


def test_refresh_federal_from_congress_rows(repo):
    row = {
        "source": "congress",
        "congress": 119,
        "type": "hr",
        "number": "610",
        "existing_record": "us-119-hr-610",
        "actions": [
            {"date": "2025-01-22", "text": "Introduced in House", "actor": "lower",
             "classification": ["introduction"]},
            {"date": "2025-03-04", "text": "On passage Passed by the Yeas and Nays: 300 - 120.",
             "actor": "lower", "classification": ["passage"]},
        ],
        "texts": [],
    }  # fmt: skip
    write_jsonl(repo.cache / "candidates" / "federal-2026-10-17.jsonl", [row])
    result = refresh(repo, today=TODAY, sources=["congress"])
    assert len(result.updates) == 1
    u = result.updates[0]
    assert [a["description"] for a in u.added_actions] == [
        "On passage Passed by the Yeas and Nays: 300 - 120."
    ]
    assert u.status_before == "in-committee" and u.status_after == "passed-chamber"
    rec = load_yaml(repo.data / "legislation/us/119/hr-610.yaml")
    assert len(rec["actions"]) == 2


def test_new_actions_respects_hand_written_last_day():
    existing = [{"date": "2025-05-01", "description": "Passed the House 90-50"}]
    fresh = [
        {"date": "2025-05-01", "description": "Passed"},
        {"date": "2025-05-01", "description": "Sent to Senate"},
        {"date": "2025-05-03", "description": "Referred to Education"},
    ]
    assert [a["description"] for a in new_actions(existing, fresh)] == ["Referred to Education"]
    same = [{"date": "2025-05-01", "description": "Passed"}]
    assert [a["description"] for a in new_actions(same, fresh)] == [
        "Sent to Senate",
        "Referred to Education",
    ]


def test_append_to_list_needs_a_block_list():
    assert append_to_list("id: x\nactions: []\n", "actions", [{"date": "2025-01-01"}]) is None
    text = "id: x\nactions:\n  - date: '2025-01-01'\nsources: []\n"
    out = append_to_list(text, "actions", [{"date": "2025-01-02"}])
    assert out == "id: x\nactions:\n  - date: '2025-01-01'\n  - date: \"2025-01-02\"\nsources: []\n"


# -- summary --------------------------------------------------------------------------------


def test_summary_lists_added_updated_and_ruled_out(repo, tmp_path):
    rec = repo.data / "legislation/us-tx/2025/hb-1481.yaml"
    run = tmp_path / "run.json"
    run.write_text(
        json.dumps(
            {
                "judgments": [
                    {"key": "us-tx-2025-hb-1481", "stage": "full", "in_scope": True,
                     "confidence": "high"},
                    {"key": key(9), "stage": "full", "in_scope": False, "confidence": "medium",
                     "reasons": "Insurance only.", "url": "https://example.org/hb9"},
                    {"key": key(8), "stage": "triage", "decision": "out"},
                ],
                "state": {"full": {"dropped": [{"key": key(7), "errors": ["quote not found"]}]}},
                "spend": {"spent_usd": 1.234, "cap_usd": 10, "requests": 5,
                          "model": "claude-sonnet-5-5", "effort": "medium"},
            }
        )
    )  # fmt: skip
    refresh_file = tmp_path / "refresh.json"
    refresh_file.write_text(
        json.dumps(
            {
                "updated": [
                    {"id": "us-tx-2025-sb-20", "path": "data/legislation/us-tx/2025/sb-20.yaml",
                     "status_before": "in-committee", "status_after": "passed-chamber",
                     "added_actions": [{"date": "2025-04-01", "description": "Passed"}],
                     "added_versions": []}
                ]
            }
        )
    )  # fmt: skip
    s = build_summary(
        repo,
        run=run,
        refresh=refresh_file,
        changes=(
            [str(rec.relative_to(repo.root))],
            ["data/legislation/us-tx/2025/sb-20.yaml", "data/jurisdictions/us-tx.yaml"],
        ),
    )
    assert [a["id"] for a in s.added] == ["us-tx-2025-hb-1481"]
    assert s.added[0]["confidence"] == "high"
    assert s.updated[0]["status_after"] == "passed-chamber"
    assert s.other_changes == ["data/jurisdictions/us-tx.yaml"]
    assert [r["key"] for r in s.ruled_out] == [key(9)] and s.triaged_out == 1
    assert s.dropped[0]["key"] == key(7)
    body = markdown(s)
    assert "Records added: 1. Records updated: 1." in body
    assert "[us-tx-2025-hb-9](https://example.org/hb9)" in body
    assert "in-committee to passed-chamber" in body and "$1.23 of the $10.00 cap" in body
    assert s.as_dict()["changed"] is True


def test_check_full_on_run_outputs_uses_stored_texts(repo):
    store_texts(repo, [1])
    report = check_full(
        [full_row(key(1), 1)], Vocab.load(repo.vocab), TextStore(ClassifyPaths(repo).texts)
    )
    assert report.ok, report.lines()
