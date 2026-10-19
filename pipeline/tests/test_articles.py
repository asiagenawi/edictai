"""`articles write`: subject choice, checks, budget, skipping, and the ledger, with the
Anthropic client faked and the site build replaced by a stub."""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from statescope.articles.check import check_article, from_reply
from statescope.articles.context import build_context
from statescope.articles.data import Article, load_articles, load_dataset
from statescope.articles.subjects import (
    Limits,
    Subject,
    candidates,
    choose,
    coverage,
    read_ledger,
    rotation,
    write_ledger_entry,
)
from statescope.articles.write import ArticleSettings, write_article
from statescope.build import build
from statescope.classify.llm import Budget, CappedBudget
from statescope.classify.settings import Settings
from statescope.classify.summary import build_summary, markdown
from statescope.yamlio import load_yaml

from .conftest import REPO, make_tree

RUN = date(2026, 10, 19)
PRICES = {"claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.2}}
LIMITS = Limits(min_records=1, min_jurisdictions=1, min_studies=1, min_roundup_items=1)
TX = "jurisdiction:us-tx"


@pytest.fixture
def repo(tmp_path):
    paths = make_tree(tmp_path / "repo")
    build(paths, today=date(2026, 10, 17), write_web=False, log=lambda _m: None)
    (paths.root / "site" / "src" / "content" / "articles" / "2026").mkdir(parents=True)
    return paths


def settings(**over):
    return Settings(prices=PRICES, spend_cap_usd=10.0, **over)


def asettings(**over):
    base = {"min_words": 20, "max_words": 400, "repair_attempts": 1, "limits": LIMITS}
    return ArticleSettings(**{**base, **over})


PARA = (
    "As of October 19, 2026, Edictai's records show that HB 1481, signed by the governor "
    "of Texas, requires each school district to adopt a policy on the use of artificial "
    "intelligence by its students and its teachers [1]."
)


def reply(slug="texas-ai-policy-laws", notes=None, paragraph=PARA, edits=True):
    out = {
        "slug": slug,
        "title": "What Texas requires of school districts on AI",
        "dek": "Texas law requires each school district to adopt an AI policy.",
        "brief": [
            "HB 1481 requires a district AI policy.",
            "The Texas Education Agency issued guidance.",
        ],
        "caption": "Texas records cited in this brief, as recorded by Edictai in October 2026.",
        "sketch_note": "a policy per district",
        "body": [
            {"p": paragraph},
            {"h": "Guidance"},
            {"p": "The Texas Education Agency also issued guidance for districts on AI [2]."},
        ],
        "notes": notes
        if notes is not None
        else [
            {"record": "us-tx-2025-hb-1481", "provision": 0},
            {"record": "us-tx-tea-ai-guidance", "provision": 0},
        ],
    }
    if edits:
        out["edits"] = ["Tightened the opening."]
    return out


def message(data, usage=(20_000, 3_000)):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps(data))],
        usage=SimpleNamespace(
            input_tokens=usage[0],
            output_tokens=usage[1],
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


class Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    """`draft` and `edit(n)` give the reply for the draft and the n-th editing call."""

    def __init__(self, draft=None, edit=None):
        self.draft = draft or (lambda: reply(edits=False))
        self.edit = edit or (lambda n: reply())
        self.calls = []
        self.messages = SimpleNamespace(stream=self.stream, create=self.stream)

    def stream(self, **params):
        self.calls.append(params)
        system = params["system"][0]["text"]
        if system.startswith("You write articles"):
            return Stream(message(self.draft()))
        n = sum(1 for p in self.calls if not p["system"][0]["text"].startswith("You write"))
        return Stream(message(self.edit(n)))


def run(repo, client, *, budget=None, build_ok=True, a=None, **kw):
    builds = []

    def stub(site):
        builds.append(site)
        return (True, "ok") if build_ok else (False, "Note 3 cites record x")

    kw.setdefault("subject_key", TX)
    result = write_article(
        repo,
        settings(),
        a or asettings(),
        budget or Budget(10.0),
        run_date=RUN,
        site=repo.root / "site",
        ledger=repo.root / ".cache" / "articles" / "ledger.json",
        client=client,
        build=stub,
        **kw,
    )
    return result, builds


def article_path(repo, slug="texas-ai-policy-laws"):
    return repo.root / "site" / "src" / "content" / "articles" / "2026" / f"{slug}.yaml"


# -- subject choice --------------------------------------------------------------------


def write_existing(repo, notes, type="State brief", slug="old", published="2026-10-18"):
    path = repo.root / "site" / "src" / "content" / "articles" / "2026" / f"{slug}.yaml"
    path.write_text(
        yaml.safe_dump(
            {"title": "Old", "dek": "Old.", "type": type, "published": published, "notes": notes}
        ),
        encoding="utf-8",
    )


def test_subjects_skip_what_existing_articles_cover(repo):
    ds = load_dataset(repo.root / "build" / "statescope.db")
    content = repo.root / "site" / "src" / "content" / "articles"
    before = {
        s.key for s in candidates(ds, coverage(ds, [], []), set(), run_date=RUN, limits=LIMITS)
    }
    assert TX in before
    write_existing(repo, [{"record": "us-tx-2025-hb-1481"}, {"record": "us-tx-eo-ga-99"}])
    cover = coverage(ds, load_articles(content), [])
    after = {s.key for s in candidates(ds, cover, set(), run_date=RUN, limits=LIMITS)}
    assert TX in cover.keys and TX not in after


def test_subjects_skip_the_ledger_and_rotate_types(repo):
    ds = load_dataset(repo.root / "build" / "statescope.db")
    ledger = [
        {
            "run_date": "2026-10-12",
            "status": "written",
            "type": "Explainer",
            "subject": "provision:requires-local-ai-policy",
        },
        {"run_date": "2026-10-05", "status": "skipped", "type": "Analysis", "subject": TX},
    ]
    cover = coverage(ds, [], ledger)
    subjects = candidates(ds, cover, set(), run_date=RUN, limits=LIMITS)
    keys = {s.key for s in subjects}
    assert "provision:requires-local-ai-policy" not in keys
    assert TX in keys  # a skipped attempt does not use the subject up
    assert rotation(cover)[-1] == "Explainer"
    assert choose(subjects, cover).type != "Explainer"


def test_subjects_prefer_this_weeks_changes(repo):
    ds = load_dataset(repo.root / "build" / "statescope.db")
    cover = coverage(ds, [], [])
    changed = {"us-tx-2025-hb-1481"}
    pick = choose(candidates(ds, cover, changed, run_date=RUN, limits=LIMITS), cover)
    assert pick.changed > 0 and "us-tx-2025-hb-1481" in pick.records


# -- checks ------------------------------------------------------------------------------


def context(repo):
    ds = load_dataset(repo.root / "build" / "statescope.db")
    ids = ["us-tx-2025-hb-1481", "us-tx-tea-ai-guidance", "us-tx-eo-ga-99"]
    return build_context(
        ds, Subject(TX, "State brief", "Texas", ids), set(), [], run_date=RUN, max_records=60
    )


def errors_for(repo, r):
    slug, art = from_reply(r, type="State brief", published="2026-10-19", release="October 2026")
    return check_article(
        slug,
        art,
        context(repo),
        type="State brief",
        published="2026-10-19",
        existing_slugs=["old"],
        min_words=20,
        max_words=400,
    )


def test_a_good_article_passes(repo):
    assert errors_for(repo, reply()) == []


@pytest.mark.parametrize(
    ("notes", "expect"),
    [
        (
            [
                {"record": "us-ca-2025-ab-1", "provision": 0},
                {"record": "us-tx-tea-ai-guidance", "provision": 0},
            ],
            "not in the data given",
        ),
        (
            [
                {"record": "us-tx-2025-hb-1481", "provision": 7},
                {"record": "us-tx-tea-ai-guidance", "provision": 0},
            ],
            "has no provision 7",
        ),
        # In the data, but not given to the model for this subject.
        (
            [
                {"record": "us-119-hr-610", "provision": 0},
                {"record": "us-tx-tea-ai-guidance", "provision": 0},
            ],
            "not in the data given",
        ),
        ([{"record": "us-tx-2025-hb-1481", "provision": 0}], "there are 1 notes"),
    ],
)
def test_invalid_citations_are_rejected(repo, notes, expect):
    assert any(expect in e for e in errors_for(repo, reply(notes=notes)))


def test_quotes_and_numbers_must_come_from_the_data(repo):
    made_up = PARA.replace("requires", "says “every district must ban chatbots” and requires")
    assert any("quotes words not found" in e for e in errors_for(repo, reply(paragraph=made_up)))
    real = PARA.replace("requires", "says “Each school district shall adopt a policy” and requires")
    assert errors_for(repo, reply(paragraph=real)) == []
    stat = PARA.replace("[1].", "[1]. It covers 4,312 districts.")
    assert any("4312" in e for e in errors_for(repo, reply(paragraph=stat)))


def test_style_and_length_rules(repo):
    assert any("question" in e for e in errors_for(repo, reply(paragraph=PARA + " Why?")))
    assert any(
        "landmark" in e for e in errors_for(repo, reply(paragraph="A landmark law. " + PARA))
    )
    assert any("words" in e for e in errors_for(repo, reply(paragraph="Short [1].")))
    assert any("already used" in e for e in errors_for(repo, reply(slug="old")))


def test_checks_pass_on_the_published_articles():
    db = REPO / "build" / "statescope.db"
    content = REPO / "site" / "src" / "content" / "articles"
    if not db.exists() or not any(content.glob("**/*.yaml")):
        pytest.skip("needs a build of the repository's data")
    ds = load_dataset(db)
    for a in load_articles(content):
        data = load_yaml(a.path)
        subject = Subject("all", data["type"], "all", list(ds.records), list(ds.studies))
        ctx = build_context(ds, subject, set(), [], run_date=RUN, max_records=10_000)
        errors = check_article(
            a.slug,
            data,
            ctx,
            type=data["type"],
            published=data["published"],
            existing_slugs=[],
            min_words=600,
            max_words=1200,
        )
        assert errors == [], (a.slug, errors)


# -- the command ------------------------------------------------------------------------------


def test_writes_the_article_after_a_draft_and_an_edit(repo):
    client = FakeClient()
    result, builds = run(repo, client)
    assert result.status == "written", result.reason
    assert len(client.calls) == 2 and len(builds) == 1
    draft, edit = client.calls
    assert draft["model"] == "claude-opus-5-5" and draft["output_config"]["effort"] == "high"
    assert "us-tx-2025-hb-1481" in draft["messages"][0]["content"]
    # The editor gets the draft and the data its notes cite, not the whole subject.
    assert "us-tx-eo-ga-99" not in edit["messages"][0]["content"]
    assert "edits" in edit["output_config"]["format"]["schema"]["properties"]
    data = load_yaml(article_path(repo))
    assert data["type"] == "State brief" and data["published"] == "2026-10-19"
    assert data["data_release"] == "October 2026" and "author" not in data
    assert data["notes"] == [
        {"record": "us-tx-2025-hb-1481"},
        {"record": "us-tx-tea-ai-guidance"},
    ]
    assert result.spend["spent_usd"] > 0 and result.spend["cap_usd"] == 3.0
    assert (repo.cache / "articles" / "written" / "texas-ai-policy-laws.yaml").exists()


def test_a_failed_check_gets_one_repair(repo):
    bad = reply(
        notes=[
            {"record": "us-tx-2025-hb-1481", "provision": 9},
            {"record": "us-tx-tea-ai-guidance", "provision": 0},
        ]
    )
    client = FakeClient(edit=lambda n: bad if n == 1 else reply())
    result, _ = run(repo, client)
    assert result.status == "written"
    assert len(client.calls) == 3
    assert "has no provision 9" in client.calls[2]["messages"][0]["content"]


def test_invalid_citations_skip_the_article(repo):
    bad = reply(
        notes=[
            {"record": "us-ca-made-up", "provision": 0},
            {"record": "us-tx-tea-ai-guidance", "provision": 0},
        ]
    )
    client = FakeClient(edit=lambda n: bad)
    result, builds = run(repo, client)
    assert result.status == "skipped" and "not in the data given" in result.reason
    assert builds == [] and not article_path(repo).exists()
    assert len(client.calls) == 3  # draft, edit, one repair
    assert read_ledger(repo.cache / "articles" / "ledger.json")[0]["status"] == "skipped"


def test_a_failed_site_build_skips_the_article(repo):
    result, builds = run(repo, FakeClient(), build_ok=False)
    assert result.status == "skipped" and "site build failed" in result.reason
    assert len(builds) == 1 and not article_path(repo).exists()


def test_the_run_cap_stops_the_article(repo):
    client = FakeClient()
    budget = Budget(10.0)
    budget.spent = 9.95
    result, _ = run(repo, client, budget=budget)
    assert result.status == "skipped" and "spending cap" in result.reason
    assert client.calls == []


def test_the_article_cap_stops_the_editing_call(repo):
    client = FakeClient()
    budget = Budget(10.0)
    # The draft (about $0.64 reserved, $0.14 spent) fits; the edit does not.
    result, _ = run(repo, client, budget=budget, a=asettings(spend_cap_usd=0.75))
    assert (
        result.status == "skipped" and "edit" in result.reason and "spending cap" in result.reason
    )
    assert len(client.calls) == 1
    assert budget.spent == pytest.approx(result.spend["spent_usd"], abs=1e-3) and budget.spent > 0


def test_capped_budget_counts_against_the_run(tmp_path):
    parent = Budget(5.0, tmp_path / "spend.json")
    sub = CappedBudget(parent, 1.0)
    assert sub.reserve(0.8)
    assert not sub.reserve(0.3) and sub.stopped
    sub.settle(0.8, 0.5)
    assert parent.spent == 0.5 and sub.spent == 0.5 and parent.reserved == 0
    assert json.loads((tmp_path / "spend.json").read_text())["spent_usd"] == 0.5


def test_the_ledger_makes_a_rerun_send_nothing(repo):
    client = FakeClient()
    first, _ = run(repo, client)
    assert first.status == "written"
    again, builds = run(repo, client)
    assert again.status == "exists" and again.slug == first.slug and builds == []
    assert len(client.calls) == 2
    # A fresh checkout (the file gone, the cache kept) gets the article back.
    article_path(repo).unlink()
    restored, builds = run(repo, client)
    assert restored.status == "exists" and article_path(repo).exists() and len(builds) == 1
    assert len(client.calls) == 2
    ledger = read_ledger(repo.cache / "articles" / "ledger.json")
    assert [e["run_date"] for e in ledger] == ["2026-10-19"]


def test_ledger_entries_replace_their_run_date(tmp_path):
    path = tmp_path / "ledger.json"
    write_ledger_entry(path, {"run_date": "2026-10-19", "status": "skipped"})
    write_ledger_entry(path, {"run_date": "2026-10-12", "status": "written"})
    write_ledger_entry(path, {"run_date": "2026-10-19", "status": "written"})
    assert read_ledger(path) == [
        {"run_date": "2026-10-12", "status": "written"},
        {"run_date": "2026-10-19", "status": "written"},
    ]


def test_the_summary_reports_the_article(repo, tmp_path):
    result, _ = run(repo, FakeClient())
    out = tmp_path / "article.json"
    out.write_text(json.dumps({**result.as_dict(), "path": "site/" + result.path}))
    s = build_summary(repo, article=out, changes=([], []))
    assert s.changed
    body = markdown(s)
    assert "What Texas requires of school districts on AI" in body
    assert "State brief" in body and "2 notes citing 2 records" in body

    skipped = tmp_path / "skipped.json"
    skipped.write_text(json.dumps({"status": "skipped", "reason": "the site build failed"}))
    s = build_summary(repo, article=skipped, changes=([], []))
    assert not s.changed and "No article this week: the site build failed" in markdown(s)
    s = build_summary(repo, article=tmp_path / "missing.json", changes=([], []))
    assert "did not finish" in markdown(s)


def test_existing_articles_load_their_notes(tmp_path):
    path = tmp_path / "2026" / "x.yaml"
    path.parent.mkdir()
    path.write_text(
        "title: T\ndek: D\ntype: Analysis\npublished: '2026-10-18'\nnotes:\n"
        "  - record: a\n  - { record: b, provision: 2 }\n  - { study: s, finding: 1 }\n"
    )
    (a,) = load_articles(tmp_path)
    assert isinstance(a, Article)
    assert a.record_notes == [("a", 0), ("b", 2)] and a.study_notes == [("s", 1)]
    assert Path(a.path).name == "x.yaml"


def test_the_drawing_fits_the_subject_and_varies(repo):
    from statescope.articles.subjects import choose_drawing

    ds = load_dataset(repo.root / "build" / "statescope.db")
    state = Subject(TX, "State brief", "Texas", ["us-tx-2025-hb-1481"])
    assert choose_drawing(ds, state, []) == "gavel"
    recent = [
        Article("a", Path("a"), "", "", "Analysis", "2026-10-18", [], [], drawing="gavel"),
        Article("b", Path("b"), "", "", "Analysis", "2026-10-17", [], [], drawing="phone"),
        Article("c", Path("c"), "", "", "Analysis", "2026-10-01", [], [], drawing="map"),
    ]
    assert choose_drawing(ds, state, recent) == "map"
    policy = Subject("provision:requires-local-ai-policy", "Analysis", "x", ["us-tx-2025-hb-1481"])
    assert choose_drawing(ds, policy, recent) == "map"
    roundup = Subject("roundup:2026-10-01..2026-10-18", "Roundup", "x", window=("a", "b"))
    assert choose_drawing(ds, roundup, recent) == "calendar"


def test_the_written_article_names_its_drawing_and_sketch_note(repo):
    run(repo, FakeClient())
    data = load_yaml(article_path(repo))
    assert data["drawing"] == "gavel" and data["sketch_note"] == "a policy per district"
    empty = reply()
    empty["sketch_note"] = ""
    assert any("sketch_note" in e for e in errors_for(repo, empty))
