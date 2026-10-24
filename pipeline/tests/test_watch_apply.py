"""`watch-apply`, with the web and `claude -p` faked."""

import json
import shutil
from datetime import date

import pytest

from edictai.classify.settings import Settings
from edictai.paths import Paths
from edictai.validate import validate
from edictai.watch import run
from edictai.watch.apply import (
    ApplySettings,
    apply_changes,
    load_apply_settings,
    pending_pages,
    render_markdown,
    render_review,
)
from edictai.watch.state import WatchState
from edictai.yamlio import load_yaml

from .conftest import REPO, make_tree
from .test_classify_claude_code import FakeClaude, result
from .test_watch import FakeWeb, page

PAGE = "https://example.org/tea/ai"
PDF = "https://example.org/tea/ai-guidance.pdf"
FRAMEWORK = "https://example.org/tea/ai-literacy-framework.html"
DOC = "us-tx-tea-ai-guidance"
DOC_PATH = "data/documents/us-tx/tea-ai-guidance.yaml"
DAY1, DAY2, DAY3 = date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 27)

V1 = """
<h2>Guidance</h2>
<p>The agency publishes guidance for districts on classroom AI use.</p>
<p><a href="/tea/ai-guidance.pdf">AI guidance (PDF)</a></p>
"""
V2 = """
<h2>Guidance</h2>
<p>The agency publishes guidance for districts on classroom AI use. The guidance was revised
as Version 3.0 in October 2026 and now requires districts to adopt an AI policy.</p>
<p><a href="/tea/ai-guidance.pdf">AI guidance (PDF)</a></p>
<p><a href="/tea/ai-literacy-framework.html">AI literacy framework</a></p>
"""
FRAMEWORK_HTML = page(
    """<h2>Texas AI Literacy Framework</h2>
<p>Districts must teach AI literacy in grades 6 through 12 beginning in the 2027-28 school
year.</p>""",
    title="Texas AI Literacy Framework",
)
PDF_TEXT = "Guidance on AI in Texas Classrooms. Version 3.0. Districts shall adopt an AI policy."


@pytest.fixture
def repo(tmp_path):
    paths = make_tree(tmp_path / "repo")
    (paths.root / "docs").mkdir()
    shutil.copy(REPO / "docs" / "methodology.md", paths.root / "docs" / "methodology.md")
    path = paths.root / DOC_PATH
    path.write_text(
        path.read_text().replace(
            "sources:\n",
            f"sources:\n  - url: {PAGE}\n    kind: official\n    accessed: 2026-10-01\n",
        )
    )
    return paths


@pytest.fixture
def web():
    w = FakeWeb()
    w.set(PAGE, page(V1))
    w.set(PDF, PDF_TEXT, content_type="text/plain")
    w.set(FRAMEWORK, FRAMEWORK_HTML)
    return w


def watch(repo, web, day):
    return run(repo, transport=web, today=day, sleep=lambda s: None)


def changed(repo, web):
    """The page observed on DAY1, then changed on DAY2."""
    watch(repo, web, DAY1)
    web.set(PAGE, page(V2))
    watch(repo, web, DAY2)


def settings():
    return Settings(backend="claude-code", call_cap=50, claude_workers=1)


def apply(repo, web, runner, **kw):
    kw.setdefault("today", DAY3)
    return apply_changes(
        repo, settings(), ApplySettings(), transport=web, runner=runner, sleep=lambda s: None, **kw
    )


def update(**over):
    out = {
        "id": DOC,
        "note": "Page says the guidance was revised as Version 3.0.",
        "version": "3.0",
        "updated": "2026-10",
        "summary": "Guidance for districts on classroom AI use, revised in Version 3.0 to require a district AI policy.",
        "evidence": [{"source_url": PAGE, "quote": "revised as Version 3.0 in October 2026"}],
    }
    out.update(over)
    return out


def new_doc(**over):
    out = {
        "slug": "tea-ai-literacy-framework",
        "title": "Texas AI Literacy Framework",
        "classification": "framework",
        "status": "current",
        "education_levels": ["k12"],
        "topics": ["ai-literacy"],
        "summary": "Requires districts to teach AI literacy in grades 6 through 12.",
        "source_url": FRAMEWORK,
        "source_title": "Texas AI Literacy Framework",
        "provisions": [
            {
                "type": "requires-ai-literacy-instruction",
                "description": "Districts teach AI literacy in grades 6-12.",
                "source_url": FRAMEWORK,
                "locator": "Framework",
                "quote": "Districts must teach AI literacy in grades 6 through 12",
            }
        ],
        "related": [DOC],
        "evidence": [{"source_url": FRAMEWORK, "quote": "Texas AI Literacy Framework"}],
    }
    out.update(over)
    return out


def reply(verdict="update", updates=(), new=(), review=()):
    return result(
        {
            "verdict": verdict,
            "what_changed": "The guidance was revised and a framework was added.",
            "updates": list(updates),
            "new_documents": list(new),
            "review": list(review),
        }
    )


def doc(repo, rel=DOC_PATH):
    return load_yaml(repo.root / rel)


# -- the watcher keeps the base --------------------------------------------------------


def test_watch_keeps_text_from_before_the_first_unreviewed_change(repo, web):
    changed(repo, web)
    web.set(PAGE, page(V2 + "<p>Another AI change.</p>"))
    watch(repo, web, DAY2.replace(day=21))
    state = WatchState.load(repo.cache / "watch")
    assert "revised" not in state.read_base(PAGE)
    assert "Another AI change" in state.read_text(PAGE)
    pages = pending_pages(state, [r for r in _orgs(repo)])
    assert [p.url for p in pages] == [PAGE]
    assert len(pages[0].changes) == 2


def _orgs(repo):
    from edictai.records import load_records

    return load_records(repo.data, ["organization"]).of("organization")


# -- applying ----------------------------------------------------------------------------


def test_update_and_new_document(repo, web):
    changed(repo, web)
    claude = FakeClaude(reply(updates=[update()], new=[new_doc()]))
    res = apply(repo, web, claude)

    [pr] = res.pages
    assert pr.verdict == "update" and pr.updated == [DOC]
    assert pr.added == ["us-tx-tea-ai-literacy-framework"] and not pr.dropped
    assert res.changed_files == [
        "data/documents/us-tx/tea-ai-guidance.yaml",
        "data/documents/us-tx/tea-ai-literacy-framework.yaml",
    ]

    # What the model was given: the diff, the record, and the linked texts.
    user = claude.calls[0]["input"]
    assert "+as Version 3.0" in user or "Version 3.0" in user.split("<diff>")[1]
    assert f"`{DOC}`" in user and FRAMEWORK in user and "Districts shall adopt" in user
    assert "--model" in claude.calls[0]["args"]
    assert claude.calls[0]["args"][claude.calls[0]["args"].index("--model") + 1] == (
        "claude-opus-5-5"
    )

    d = doc(repo)
    assert d["version"] == "3.0" and d["updated"] == "2026-10"
    assert d["summary"].endswith("district AI policy.")
    rev = d["revisions"][-1]
    assert rev["observed"] == DAY2.isoformat() and rev["url"] == PAGE
    assert len(rev["sha256"]) == 64 and rev["note"].startswith("Page says")
    assert d["verification"] == {
        "status": "machine-verified",
        "last_verified": DAY3.isoformat(),
        "method": "change-watcher",
    }
    accessed = {s["url"]: s["accessed"] for s in d["sources"]}
    assert accessed == {PAGE: DAY3.isoformat(), PDF: DAY3.isoformat()}
    # Keys keep the schema's order.
    keys = list(d)
    assert keys.index("version") < keys.index("revisions") < keys.index("sources")

    new = doc(repo, "data/documents/us-tx/tea-ai-literacy-framework.yaml")
    assert new["id"] == "us-tx-tea-ai-literacy-framework" and new["issuer"] == "us-tx-k12-agency"
    assert [s["url"] for s in new["sources"]] == [FRAMEWORK, PAGE]
    assert new["provisions"][0]["citation"]["source_url"] == FRAMEWORK
    report, _ = validate(repo, today=DAY3)
    assert report.ok, [i.format() for i in report.errors]

    # Reviewed: the next run has nothing to do.
    state = WatchState.load(repo.cache / "watch")
    assert state.applied[PAGE]["verdict"] == "update"
    assert state.read_base(PAGE) == state.read_text(PAGE)
    again = FakeClaude(reply())
    assert apply(repo, web, again).pages == [] and again.calls == []

    md = render_markdown(res)
    assert f"Updated `{DOC}`" in md and "Added `us-tx-tea-ai-literacy-framework`" in md
    assert render_review(res) == ""


def test_quote_not_in_the_text_drops_the_change(repo, web):
    changed(repo, web)
    before = (repo.root / DOC_PATH).read_text()
    bad = update(evidence=[{"source_url": PAGE, "quote": "Version 4.0 was issued"}])
    res = apply(repo, web, FakeClaude(reply(updates=[bad])))
    [pr] = res.pages
    assert pr.updated == [] and "quote not found" in pr.dropped[0]
    assert (repo.root / DOC_PATH).read_text() == before
    assert res.changed_files == []
    assert "Dropped: update to us-tx-tea-ai-guidance" in render_review(res)
    # Still reviewed; the person reads the issue.
    assert WatchState.load(repo.cache / "watch").applied[PAGE]["sha256"]


def test_only_records_given_may_be_updated(repo, web):
    changed(repo, web)
    res = apply(repo, web, FakeClaude(reply(updates=[update(id="us-tx-2025-hb-1481")])))
    assert "not one of the records given" in res.pages[0].dropped[0]
    assert res.changed_files == []


def test_new_document_already_in_the_data_is_dropped(repo, web):
    changed(repo, web)
    res = apply(repo, web, FakeClaude(reply(new=[new_doc(source_url=PDF, provisions=[],
        evidence=[{"source_url": PDF, "quote": "Version 3.0"}])])))  # fmt: skip
    assert "already a source of a record" in res.pages[0].dropped[0]


def test_record_failing_validation_is_put_back(repo, web):
    changed(repo, web)
    before = (repo.root / DOC_PATH).read_text()
    res = apply(
        repo,
        web,
        FakeClaude(reply(updates=[update(status="superseded", superseded_by="us-tx-nothing")])),
    )
    [pr] = res.pages
    assert pr.updated == [] and "fails validation" in pr.dropped[0]
    assert (repo.root / DOC_PATH).read_text() == before


def test_noise_changes_nothing(repo, web):
    changed(repo, web)
    res = apply(repo, web, FakeClaude(reply(verdict="noise")))
    assert res.pages[0].verdict == "noise" and res.changed_files == []
    assert not res.changed
    assert WatchState.load(repo.cache / "watch").applied[PAGE]["verdict"] == "noise"


def test_unanswered_page_stays_pending(repo, web):
    changed(repo, web)
    failed = result(None, text="no", is_error=True, subtype="error_during_execution")
    res = apply(repo, web, FakeClaude(failed))
    assert res.pages[0].error and res.pages[0].verdict is None
    state = WatchState.load(repo.cache / "watch")
    assert PAGE not in state.applied
    assert "revised" not in state.read_base(PAGE)
    assert "Not reviewed" in render_review(res)


def test_dry_run_writes_nothing(repo, web):
    changed(repo, web)
    before = (repo.root / DOC_PATH).read_text()
    state_before = (repo.cache / "watch" / "state.json").read_text()
    res = apply(repo, web, FakeClaude(reply(updates=[update()])), save=False)
    assert res.pages[0].verdict == "update" and res.changed_files == []
    assert (repo.root / DOC_PATH).read_text() == before
    assert (repo.cache / "watch" / "state.json").read_text() == state_before


def test_settings_from_automation_yaml(repo):
    (repo.root / "pipeline").mkdir()
    (repo.root / "pipeline" / "automation.yaml").write_text(
        "watch:\n  model: claude-sonnet-5-5\n  claude_code_calls: 5\n"
    )
    s = load_apply_settings(repo)
    assert s.model == "claude-sonnet-5-5" and s.claude_code_calls == 5 and s.effort == "high"


def test_shipped_settings_load():
    s = load_apply_settings(Paths(REPO))
    assert s.model == "claude-opus-5-5"
    json.dumps(s.__dict__)
