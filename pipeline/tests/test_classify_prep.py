import json
from pathlib import Path

import pytest

from statescope.classify.common import (
    ClassifyPaths,
    bill_key,
    latest_text,
    load_candidates,
    read_jsonl,
    slug_for,
    write_jsonl,
)
from statescope.classify.instructions import full_md, scope_section, triage_md, write_instructions
from statescope.classify.prep import excerpts, full_item, prep_full, prep_triage, triage_item
from statescope.classify.texts import TextStore
from statescope.paths import Paths
from statescope.vocab import Vocab

REPO = Path(__file__).resolve().parents[2]


def candidate(bill_id=1, number="HB 10", session="2025", jurisdiction="us-tx", **extra):
    row = {
        "bill_id": bill_id,
        "state": jurisdiction[3:].upper() if jurisdiction != "us" else "US",
        "jurisdiction": jurisdiction,
        "session": session,
        "legiscan_session_id": 2160,
        "bill_number": number.replace(" ", ""),
        "identifier": number,
        "bill_type": "B",
        "title": "Relating to artificial intelligence in public schools",
        "description": "Relating to artificial intelligence in public schools",
        "status": 1,
        "status_text": "Introduced",
        "status_date": "2025-02-01",
        "history": [{"date": "2025-02-01", "action": "Filed", "chamber": "H"}],
        "sponsors": [{"name": "Jane Doe", "party": "R", "role": "Rep", "sponsor_type_id": 1}],
        "texts": [
            {
                "doc_id": 100 + bill_id,
                "type": "Introduced",
                "date": "2025-02-01",
                "mime": "text/html",
                "url": "https://legiscan.com/TX/text/HB10/id/1",
                "state_link": "https://capitol.texas.gov/tlodocs/89R/billtext/html/HB00010I.htm",
            }
        ],
        "url": "https://capitol.texas.gov/BillLookup/History.aspx?LegSess=89R&Bill=HB10",
        "matched_by": "title",
        "matched_keywords": ["artificial intelligence"],
        "trigger_strength": "strong",
        "fulltext_queries": [],
        "existing_record": None,
    }
    row.update(extra)
    return row


@pytest.fixture
def cp(tmp_path: Path) -> ClassifyPaths:
    (tmp_path / "data").mkdir()
    return ClassifyPaths(Paths(tmp_path))


@pytest.mark.parametrize(
    ("identifier", "slug"),
    [
        ("HB 1481", "hb-1481"),
        ("H.R. 610", "hr-610"),
        ("B26-0491", "b26-491"),
        ("SB 24-205", "sb-24-205"),
    ],
)
def test_slug_for(identifier, slug):
    assert slug_for(identifier) == slug


def test_bill_key():
    assert bill_key("us-tx", "2025", "HB 1481") == "us-tx-2025-hb-1481"
    assert bill_key("us", "119", "H.R. 5351") == "us-119-hr-5351"


def test_load_candidates_keeps_latest_carryover(tmp_path):
    early = candidate(1, "SB 224", "2025-2026", "us-ok")
    late = candidate(2, "SB 224", "2025-2026", "us-ok")
    late["history"] = [{"date": "2026-03-01", "action": "Passed", "chamber": "S"}]
    path = tmp_path / "c.jsonl"
    write_jsonl(path, [early, late])
    rows = load_candidates(path)
    assert list(rows) == ["us-ok-2025-2026-sb-224"]
    row = rows["us-ok-2025-2026-sb-224"]
    assert row["bill_id"] == 2
    assert row["other_bill_ids"] == [1]


def test_latest_text_ignores_placeholder_dates():
    texts = [
        {"doc_id": 5, "date": "2025-03-01"},
        {"doc_id": 9, "date": "0000-00-00"},
        {"doc_id": 7, "date": "2025-04-01"},
    ]
    assert latest_text(texts)["doc_id"] == 7


def test_triage_item_is_compact():
    long = "Requires " + "school districts to do things; " * 40 + "and artificial intelligence."
    row = {**candidate(), "key": "us-tx-2025-hb-10", "description": long}
    item = triage_item(row)
    assert item["key"] == "us-tx-2025-hb-10"
    assert len(item["description"]) <= 700
    assert item["snippets"] and "artificial intelligence" in item["snippets"][0]
    assert "history" not in item and "sponsors" not in item
    assert item["status"].startswith("Introduced; last action 2025-02-01: Filed")


def test_prep_triage_batches_and_skips(cp):
    rows = {
        r["key"]: r
        for r in (
            {**candidate(i, f"HB {i}"), "key": bill_key("us-tx", "2025", f"HB {i}")}
            for i in range(1, 8)
        )
    }
    rows["us-tx-2025-hb-3"]["existing_record"] = "us-tx-2025-hb-3"
    result = prep_triage(cp, rows, batch_size=4)
    assert [p.name for p in result.batches] == ["batch-001.jsonl", "batch-002.jsonl"]
    assert result.items == 6 and result.skipped_existing == 1
    keys = [r["key"] for p in result.batches for r in read_jsonl(p)]
    assert "us-tx-2025-hb-3" not in keys
    manifest = json.loads((cp.stage_dir("triage") / "manifest.json").read_text())
    assert [m["items"] for m in manifest] == [4, 2]
    # A second run places nothing twice; new candidates go to a new batch.
    rows["us-tx-2025-hb-9"] = {**candidate(9, "HB 9"), "key": "us-tx-2025-hb-9"}
    again = prep_triage(cp, rows, batch_size=4)
    assert again.skipped_assigned == 6
    assert [p.name for p in again.batches] == ["batch-003.jsonl"]
    assert [r["key"] for r in read_jsonl(again.batches[0])] == ["us-tx-2025-hb-9"]


def test_excerpts_prefer_ai_and_education_and_respect_cap():
    filler = "The commissioner shall adopt rules regarding fees for licenses. " * 30
    text = (
        "AN ACT relating to state government.\n"
        + filler
        + "\nSec. 4. Each school district shall adopt a policy on student use of artificial intelligence.\n"
        + filler
        + "\nSec. 9. The agency may study artificial intelligence in procurement.\n"
        + filler
    )
    ex = excerpts(text, cap=1500)
    joined = " ".join(e["text"] for e in ex.excerpts)
    assert ex.excerpts[0]["text"].startswith("AN ACT relating to state government.")
    assert (
        "Each school district shall adopt a policy on student use of artificial intelligence."
        in joined
    )
    assert ex.excerpt_chars <= 1500
    assert not ex.complete
    assert any(e.get("heading") == "Sec. 4." for e in ex.excerpts)


def test_excerpts_short_text_is_complete():
    text = "AN ACT relating to artificial intelligence in schools.\nSection 1. Districts shall teach AI.\n"
    ex = excerpts(text)
    assert ex.complete and not ex.truncated
    assert (
        ex.excerpts[0]["text"]
        == "AN ACT relating to artificial intelligence in schools. Section 1. Districts shall teach AI."
    )


def test_full_item_and_prep_full(cp):
    row = {**candidate(), "key": "us-tx-2025-hb-10"}
    store = TextStore(cp.texts)
    store.store(
        row["key"],
        {
            "doc_id": 101,
            "key": row["key"],
            "status": "ok",
            "type": "Introduced",
            "date": "2025-02-01",
            "state_link": row["texts"][0]["state_link"],
        },
        "AN ACT relating to artificial intelligence.\nSECTION 1. Each school district shall adopt a policy.\n",
    )
    store.save_index()
    item = full_item(row, None, TextStore(cp.texts), {"decision": "in", "reason": "x"})
    assert item["versions"][0]["url"].startswith("https://capitol.texas.gov/")
    assert item["sponsors"] == [{"name": "Jane Doe", "party": "R", "role": "Rep", "primary": True}]
    assert item["text"]["status"] == "ok" and item["text"]["excerpts"]
    assert item["triage"]["decision"] == "in"

    write_jsonl(
        cp.out_dir("triage") / "batch-001.jsonl",
        [{"key": row["key"], "decision": "in", "reason": "x"}],
    )
    result = prep_full(cp, {row["key"]: row}, batch_size=10)
    assert result.items == 1 and not result.without_text


def test_instructions_follow_methodology():
    paths = Paths(REPO)
    vocab = Vocab.load(paths.vocab)
    scope = scope_section(REPO / "docs" / "methodology.md")
    assert "Borderline cases" in scope
    triage, full = triage_md(paths, vocab), full_md(paths, vocab)
    # Every borderline row reaches both instruction files verbatim.
    rows = [ln for ln in scope.splitlines() if ln.startswith("| ") and "Treatment" not in ln]
    assert rows
    for row in rows:
        assert row in triage and row in full
    for t in vocab.topics:
        assert f"`{t['code']}`" in full
    for p in vocab.provision_types:
        assert f"`{p['code']}`" in full


def test_write_instructions_uses_workdir(tmp_path):
    written = write_instructions(Paths(REPO), tmp_path / "work")
    assert set(written) == {"TRIAGE.md", "FULL.md", "triage.schema.json", "full.schema.json"}
    assert str(tmp_path / "work") in written["FULL.md"].read_text()
