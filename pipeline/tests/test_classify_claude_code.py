"""The claude-code backend of `Judge`, with `claude -p` faked."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from edictai.classify.common import ClassifyPaths
from edictai.classify.llm import (
    Budget,
    CappedBudget,
    ClaudeCodeError,
    Judge,
    Request,
    make_budget,
    spend_line,
)
from edictai.classify.run import run_workdir
from edictai.classify.settings import Settings, SettingsError, load_settings, resolve_backend
from edictai.paths import Paths

from .conftest import REPO
from .test_classify_run import (  # noqa: F401  (repo is a fixture)
    PRICES,
    TODAY,
    candidate,
    full_row,
    repo,
    store_texts,
    triage_row,
    write_candidates,
)

SCHEMA = {
    "type": "object",
    "properties": {"judgments": {"type": "array", "items": {"type": "object"}}},
    "required": ["judgments"],
    "additionalProperties": False,
}


def result(data=None, *, text=None, usage=(1000, 200), cost=0.01, **over):
    out = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 2,
        "stop_reason": "tool_use",
        "result": text if text is not None else json.dumps(data),
        "total_cost_usd": cost,
        "usage": {
            "input_tokens": usage[0],
            "output_tokens": usage[1],
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }
    if data is not None:
        out["structured_output"] = data
    out.update(over)
    return out


class FakeClaude:
    """Stands in for subprocess.run: each call takes the next scripted reply, a dict
    (printed as the JSON result), an exception (raised), or a function of the call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, args, **kw):
        flags = dict(zip(args, [*args[1:], None], strict=False))
        system = Path(flags["--system-prompt-file"]).read_text(encoding="utf-8")
        self.calls.append({"args": args, "system": system, **kw})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if callable(reply) and not isinstance(reply, dict):
            reply = reply(kw["input"], system)
        if isinstance(reply, BaseException):
            raise reply
        return SimpleNamespace(stdout=json.dumps(reply) + "\n", stderr="", returncode=0)


def cc_settings(**over):
    base = dict(
        prices=PRICES,
        backend="claude-code",
        call_cap=50,
        claude_workers=1,
        items_per_request={"triage": 2, "full": 1},
        max_tokens={"triage": 1000, "full": 1000},
    )
    base.update(over)
    return Settings(**base)


def cc_judge(runner, budget=None, **over):
    s = cc_settings(**over)
    return Judge(s, budget or make_budget(s), runner=runner), s


def req(rid="r-1", user="items"):
    return Request(rid, "# Triage\nInstructions.", user, SCHEMA, 1000)


# -- one call --------------------------------------------------------------------------


def test_call_runs_claude_headless_with_no_tools(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-api-secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-secret")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "outer-session")
    fake = FakeClaude(result({"judgments": [{"key": "a"}]}))
    j, s = cc_judge(fake, effort="high")
    reply = j.call(req(user="the items"))
    assert reply.error is None and reply.data == {"judgments": [{"key": "a"}]}
    assert reply.via == "claude-code" and reply.usage["output_tokens"] == 200
    call = fake.calls[0]
    args = call["args"]
    assert args[:2] == ["claude", "-p"]
    flags = dict(zip(args, [*args[1:], None], strict=False))
    assert flags["--output-format"] == "json" and flags["--model"] == s.model
    assert flags["--effort"] == "high" and flags["--tools"] == ""
    assert flags["--setting-sources"] == "" and flags["--max-turns"] == "4"
    assert json.loads(flags["--json-schema"]) == SCHEMA
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in args
    assert call["system"] == "# Triage\nInstructions." and call["input"] == "the items"
    assert call["timeout"] == s.claude_timeout_seconds and Path(call["cwd"]).name == "work"
    env = call["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-secret"
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDECODE" not in env
    assert "CLAUDE_CODE_SESSION_ID" not in env and env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "1000"
    # The secrets are never in the arguments.
    assert not any("secret" in a for a in args)
    assert j.budget.as_dict()["calls"] == 1 and j.budget.usd_equivalent == pytest.approx(0.01)


def test_malformed_reply_is_asked_once_more():
    fake = FakeClaude(
        result(text="Sure! Here are the judgments."),
        result({"judgments": [{"key": "a"}]}),
    )
    j, _ = cc_judge(fake)
    reply = j.call(req())
    assert reply.error is None and reply.data == {"judgments": [{"key": "a"}]}
    assert len(fake.calls) == 2 and j.budget.spent == 2


def test_malformed_twice_is_an_error():
    fake = FakeClaude(result(text="not json"))
    j, _ = cc_judge(fake)
    reply = j.call(req())
    assert reply.data is None and "not JSON" in reply.error and reply.sent
    assert len(fake.calls) == 2


def test_json_in_the_text_is_parsed_and_checked_against_the_schema():
    fenced = result(text='```json\n{"judgments": []}\n```')
    j, _ = cc_judge(FakeClaude(fenced))
    assert j.call(req()).data == {"judgments": []}
    wrong = FakeClaude(result(text='{"other": 1}'), result(text='{"judgments": []}'))
    j, _ = cc_judge(wrong)
    assert j.call(req()).data == {"judgments": []} and len(wrong.calls) == 2


def test_structured_output_retries_exhausted_is_asked_once_more():
    fake = FakeClaude(
        result(text="", is_error=True, subtype="error_max_structured_output_retries"),
        result({"judgments": []}),
    )
    j, _ = cc_judge(fake)
    assert j.call(req()).data == {"judgments": []} and len(fake.calls) == 2


def test_timeout_is_an_error_and_counts_as_a_call():
    fake = FakeClaude(subprocess.TimeoutExpired(["claude"], 5))
    j, _ = cc_judge(fake, claude_timeout_seconds=5)
    reply = j.call(req())
    assert reply.error == "Claude Code timed out after 5s" and reply.sent
    assert len(fake.calls) == 1 and j.budget.spent == 1


def test_call_cap_holds_requests_back():
    fake = FakeClaude(result({"judgments": []}))
    j, _ = cc_judge(fake, call_cap=2)
    replies = j.run([req(f"r-{n}") for n in range(3)])
    assert len(fake.calls) == 2 and j.budget.stopped
    assert not replies["r-2"].sent and "spending cap" in replies["r-2"].error
    assert spend_line(j.budget.as_dict()).startswith("Used 2 of 2 Claude Code calls")


def test_usage_limit_stops_the_run():
    limit = result(text="Claude AI usage limit reached", is_error=True, api_error_status=429)
    fake = FakeClaude(limit, result({"judgments": []}))
    j, _ = cc_judge(fake)
    replies = j.run([req(f"r-{n}") for n in range(3)])
    assert len(fake.calls) == 1 and j.budget.exhausted
    assert all(not r.sent and "spending cap" in r.error for r in replies.values())


def test_auth_failure_and_missing_executable_stop_the_command():
    fake = FakeClaude(result(text="Not logged in · Please run /login", is_error=True))
    j, _ = cc_judge(fake)
    with pytest.raises(ClaudeCodeError, match="authenticate"):
        j.call(req())
    assert j.budget.spent == 0
    j, _ = cc_judge(FakeClaude(FileNotFoundError("claude")))
    with pytest.raises(ClaudeCodeError, match="not installed"):
        j.call(req())


def test_no_batches_with_claude_code():
    fake = FakeClaude(result({"judgments": []}))
    j, _ = cc_judge(fake, use_batches=True)
    j.run([req("r-1"), req("r-2")])
    assert j.batches == [] and len(fake.calls) == 2


# -- the run -----------------------------------------------------------------------------


def respond(stdin, system):
    body = stdin.split("one JSON object per line):\n\n", 1)[1].split("\n\nYour previous", 1)[0]
    keys = [json.loads(line)["key"] for line in body.strip().splitlines()]
    make = triage_row if system.startswith("# Triage") else full_row
    return result({"judgments": [make(k, 1) for k in keys]})


def test_run_workdir_through_claude_code(repo):  # noqa: F811
    write_candidates(repo, [candidate(1), candidate(2), candidate(3)])
    store_texts(repo, [1, 2, 3])
    fake = FakeClaude(respond)
    j, s = cc_judge(fake)
    res = run_workdir(ClassifyPaths(repo), j, s, today=TODAY, env={}, fetch=False)
    assert len(res.triage.judged) == 3 and len(res.full.judged) == 3
    # Two triage calls (two items each, at most) and one full call per bill.
    assert len(fake.calls) == 5 and j.budget.as_dict()["calls"] == 5


def test_run_workdir_call_cap_sends_the_rest_back(repo):  # noqa: F811
    write_candidates(repo, [candidate(1), candidate(2), candidate(3)])
    store_texts(repo, [1, 2, 3])
    fake = FakeClaude(respond)
    j, s = cc_judge(fake, call_cap=3)
    res = run_workdir(ClassifyPaths(repo), j, s, today=TODAY, env={}, fetch=False)
    assert len(fake.calls) == 3
    assert len(res.full.judged) == 1 and len(res.full.not_sent) == 2 and res.full.dropped == []


# -- backend selection and budgets -------------------------------------------------------


def test_backend_selection():
    assert resolve_backend({}) == "api"
    assert resolve_backend({"CLAUDE_CODE_OAUTH_TOKEN": "t"}) == "claude-code"
    assert resolve_backend({"CLAUDE_CODE_OAUTH_TOKEN": " "}) == "api"
    assert resolve_backend({"EDICTAI_LLM_BACKEND": "auto", "CLAUDE_CODE_OAUTH_TOKEN": "t"}) == (
        "claude-code"
    )
    assert resolve_backend({"EDICTAI_LLM_BACKEND": "api", "CLAUDE_CODE_OAUTH_TOKEN": "t"}) == "api"
    assert resolve_backend({"EDICTAI_LLM_BACKEND": "Claude-Code"}) == "claude-code"
    with pytest.raises(SettingsError):
        resolve_backend({"EDICTAI_LLM_BACKEND": "bedrock"})
    loaded = load_settings(Paths(REPO), env={"CLAUDE_CODE_OAUTH_TOKEN": "t"})
    assert loaded.backend == "claude-code" and loaded.call_cap == 400
    assert make_budget(loaded).unit == "calls" and make_budget(loaded).cap == 400
    api = load_settings(Paths(REPO), env={})
    assert api.backend == "api" and make_budget(api).unit == "usd"
    assert make_budget(api).cap == api.spend_cap_usd


def test_call_ledger_is_shared_and_kept_apart_from_dollars(tmp_path):
    ledger = tmp_path / "spend.json"
    first = Budget(10, ledger, unit="calls")
    assert first.reserve(1)
    first.settle(1, 1, usd=0.5)
    data = json.loads(ledger.read_text())
    assert data["unit"] == "calls" and data["calls"] == 1 and data["usd_equivalent"] == 0.5
    again = Budget(10, ledger, unit="calls")
    assert again.spent == 1 and again.usd_equivalent == 0.5
    # A dollar budget does not read a call ledger as dollars.
    assert Budget(10, ledger).spent == 0
    sub = CappedBudget(again, 2)
    assert sub.reserve(1) and sub.reserve(1) and not sub.reserve(1)
    d = sub.as_dict()
    assert d["unit"] == "calls" and d["cap_calls"] == 2 and d["run_cap_calls"] == 10


def test_summary_reports_calls(repo, tmp_path):  # noqa: F811
    from edictai.classify.summary import build_summary, markdown

    run = tmp_path / "run.json"
    spend = {"unit": "calls", "calls": 12, "cap_calls": 400, "usd_equivalent": 3.5,
             "requests": 12, "model": "claude-sonnet-5-5", "effort": "medium"}  # fmt: skip
    run.write_text(json.dumps({"judgments": [], "spend": spend}))
    body = markdown(build_summary(repo, run=run, changes=([], [])))
    assert "12 of the 400 Claude Code calls, $3.50 at API prices" in body
