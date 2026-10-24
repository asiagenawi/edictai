"""Federal candidates (Congress.gov bills, Federal Register documents) in the classify stages."""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from edictai.classify.common import (
    ClassifyPaths,
    candidate_key,
    is_federal_file,
    load_candidates,
    read_jsonl,
)
from edictai.classify.federal import (
    actions,
    order_versions,
    refresh_existing,
    triage_item,
    version_id,
)
from edictai.classify.federal_import import eo_status, import_federal
from edictai.classify.federal_texts import Fetcher, fetch_federal_texts
from edictai.classify.instructions import triage_md, write_instructions
from edictai.classify.judgments import check_full
from edictai.classify.prep import prep_full, prep_triage
from edictai.classify.texts import TextStore
from edictai.federal.congress import CongressClient
from edictai.ingest.http import RateLimiter
from edictai.paths import Paths
from edictai.validate import validate
from edictai.vocab import Vocab
from edictai.yamlio import load_yaml

from .conftest import REPO, make_tree

TODAY = date(2026, 10, 17)
KEY = "not-a-real-key-123"

BILL_IH = (
    "<html><body><pre>\n[Congressional Bills 119th Congress]\n"
    "[H.R. 7 Introduced in House (IH)]\n\nA BILL\n\nTo support artificial intelligence "
    "education.\n\nSECTION 1. SHORT TITLE.\n\nThis Act may be cited as the AI Classrooms Act.\n"
    "\nSEC. 2. GRANTS.\n\n(a) In General.--The Secretary of Education shall award grants to "
    "local educational agencies to provide professional development for teachers on the use "
    "of artificial intelligence in the classroom.\n</pre></body></html>"
)
BILL_RH = (
    "<html><body><pre>\n[Congressional Bills 119th Congress]\n"
    "[H.R. 7 Reported in House (RH)]\n\nA BILL\n\nTo support education technology.\n\n"
    "SECTION 1. SHORT TITLE.\n\nThis Act may be cited as the Classrooms Act.\n\nSEC. 2. "
    "GRANTS.\n\n(a) In General.--The Secretary of Education shall award grants to local "
    "educational agencies to provide professional development for teachers on instructional "
    "technology and digital tools of every kind.\n</pre></body></html>"
)
EO_TEXT = (
    "<html><body><pre>\n[Federal Register Volume 90]\n[[Page 100]]\n\nExecutive Order 14999 "
    "of May 1, 2025\n\nAI in Schools\n\nBy the authority vested in me as President, it is "
    "hereby ordered:\n\nSection 1. Policy. It is the policy of the United States to promote "
    "artificial intelligence literacy among students.\n\nSec. 2. Guidance. The Secretary of "
    "Education shall issue guidance to States on the use of artificial intelligence in "
    "classrooms within 90 days of the date of this order.\n</pre></body></html>"
)

BILL_ROW = {
    "source": "congress",
    "jurisdiction": "us",
    "congress": 119,
    "session": "119",
    "type": "hr",
    "number": "7",
    "identifier": "H.R. 7",
    "record_id": "us-119-hr-7",
    "classification": "bill",
    "title": "AI Classrooms Act",
    "official_title": "To support artificial intelligence education.",
    "titles": ["AI Classrooms Act"],
    "summary": "This bill requires the Department of Education to award grants for teacher "
    "training on artificial intelligence.",
    "policy_area": "Education",
    "matched_in": ["title"],
    "matched_keywords": ["artificial intelligence", "AI"],
    "latest_action": {"date": "2025-03-04", "text": "Reported by the Committee."},
    "actions": [
        {
            "date": "2025-02-03",
            "text": "Introduced in House",
            "actor": "lower",
            "classification": ["introduction"],
        },
        {
            "date": "2025-02-03",
            "text": "Introduced in House",
            "actor": "lower",
            "classification": ["introduction"],
        },
        {
            "date": "2025-02-03",
            "text": "Referred to the House Committee on Education and Workforce.",
            "actor": "lower",
            "classification": ["referral-committee"],
        },
        {
            "date": "2025-03-04",
            "text": "Reported by the Committee.",
            "actor": "committee",
            "classification": ["committee-passage"],
        },
        {
            "date": "2027-01-01",
            "text": "Future action.",
            "actor": "lower",
            "classification": ["other"],
        },
    ],
    "sponsors": [
        {"name": "Rep. Doe, Jane [D-CA-1]", "party": "D", "role": "sponsor", "state": "CA"},
        {"name": "Sen. Roe, Rich [R-TX]", "party": "R", "role": "cosponsor", "state": "TX"},
        {
            "name": "Rep. Poe, Pat [R-OH-2]",
            "party": "R",
            "role": "cosponsor",
            "withdrawn_date": "2025-02-10",
        },
    ],
    "texts": [
        {
            "type": "Introduced in House",
            "date": "2025-02-03",
            "url": "https://www.congress.gov/119/bills/hr7/BILLS-119hr7ih.htm",
        }
    ],
    "url": "https://www.congress.gov/bill/119th-congress/house-bill/7",
    "external_ids": {"congress_gov": "119/hr/7"},
    "existing": False,
    "existing_record": None,
}
EO_ROW = {
    "source": "federal_register",
    "jurisdiction": "us",
    "document_number": "2025-09999",
    "type": "Presidential Document",
    "subtype": "Executive Order",
    "title": "AI in Schools",
    "publication_date": "2025-05-05",
    "signing_date": "2025-05-01",
    "executive_order_number": "14999",
    "agency": "Executive Office of the President",
    "agencies": [{"name": "Executive Office of the President", "slug": "executive-office"}],
    "citation": "90 FR 100",
    "html_url": "https://www.federalregister.gov/documents/2025/05/05/2025-09999/ai-in-schools",
    "abstract": None,
    "existing": False,
    "existing_record": None,
}
NOTICE_ROW = {
    **EO_ROW,
    "document_number": "2025-08888",
    "type": "Notice",
    "subtype": None,
    "executive_order_number": None,
    "signing_date": None,
    "title": "Applications for New Awards; AI Literacy Program",
    "agency": "Education Department",
    "agencies": [{"name": "Education Department", "slug": "education-department"}],
    "abstract": "The Department invites applications for grants on artificial intelligence "
    "literacy in schools.",
    "html_url": "https://www.federalregister.gov/documents/2025/05/05/2025-08888/awards",
}


def _write_candidates(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


@pytest.fixture
def tree(tmp_path):
    paths = make_tree(tmp_path)
    for org in ("us-president", "us-ed"):
        shutil.copy(
            REPO / "data" / "organizations" / f"{org}.yaml",
            paths.data / "organizations" / f"{org}.yaml",
        )
    shutil.copytree(REPO / "docs", tmp_path / "docs", dirs_exist_ok=True)
    return paths


@pytest.fixture
def cp(tree):
    return ClassifyPaths(tree, federal=True)


def _candidates(tree, *rows):
    path = _write_candidates(tree.cache / "candidates" / "federal-2026-10-17.jsonl", rows)
    cands = load_candidates(path)
    refresh_existing(cands, tree.data)
    return cands


# -- keys, paths, and candidates ----------------------------------------------------


def test_keys_and_paths(tree):
    assert candidate_key(BILL_ROW) == "us-119-hr-7"
    assert candidate_key(EO_ROW) == "us-eo-14999"
    assert candidate_key(NOTICE_ROW) == "us-fr-2025-08888"
    state = ClassifyPaths(tree)
    federal = ClassifyPaths(tree, federal=True)
    assert state.root == tree.cache / "classify" and state.texts == tree.cache / "texts"
    assert federal.root == tree.cache / "classify" / "federal"
    assert federal.texts == tree.cache / "texts" / "federal"
    path = _write_candidates(tree.cache / "candidates" / "x.jsonl", [BILL_ROW])
    assert is_federal_file(path)


def test_refresh_existing_finds_records_added_after_the_search(tree):
    hr610 = {**BILL_ROW, "number": "610", "identifier": "H.R. 610", "record_id": "us-119-hr-610"}
    cands = _candidates(tree, BILL_ROW, hr610)
    assert cands["us-119-hr-610"]["existing_record"] == "us-119-hr-610"
    assert cands["us-119-hr-7"]["existing_record"] is None


# -- triage ----------------------------------------------------------------------------


def test_triage_items_and_batches(tree, cp):
    hr610 = {**BILL_ROW, "number": "610", "identifier": "H.R. 610", "record_id": "us-119-hr-610"}
    cands = _candidates(tree, BILL_ROW, hr610, EO_ROW, NOTICE_ROW)
    bill = triage_item(cands["us-119-hr-7"])
    assert bill["session"] == "119" and bill["bill_type"] == "hr"
    assert bill["description"].startswith("This bill requires")
    assert bill["matched_in"] == ["title"] and "official_title" in bill
    notice = triage_item(cands["us-fr-2025-08888"])
    assert notice["record_type"] == "document" and notice["document_type"] == "Notice"
    assert notice["identifier"] == "FR Doc. 2025-08888"
    assert notice["matched_keywords"][0] == "artificial intelligence"
    eo = triage_item(cands["us-eo-14999"])
    assert eo["record_type"] == "executive-action" and eo["identifier"] == "EO 14999"
    result = prep_triage(cp, cands, batch_size=150)
    assert result.skipped_existing == 1 and result.items == 3
    assert result.batches[0].parent == tree.cache / "classify" / "federal" / "triage"
    # The state working directory is untouched.
    assert not (tree.cache / "classify" / "triage").exists()


def test_federal_instructions_only_in_federal_workdir(tree, cp):
    vocab = Vocab.load(tree.vocab)
    assert "## Federal items" not in triage_md(tree, vocab)
    written = write_instructions(tree, cp.root, federal=True)
    text = written["TRIAGE.md"].read_text(encoding="utf-8")
    assert "## Federal items" in text and ".cache/classify/federal/triage" in text
    schema = json.loads(written["full.schema.json"].read_text(encoding="utf-8"))
    assert "classification" in schema["properties"]


# -- versions and actions --------------------------------------------------------------


def test_order_versions_places_undated_enrolled_bill_last():
    rows = [
        {"type": "Enrolled Bill", "date": None, "url": "https://x/BILLS-118hr1enr.htm"},
        {
            "type": "Introduced in House",
            "date": "2023-01-09",
            "url": "https://x/BILLS-118hr1ih.htm",
        },
        {"type": "Engrossed in House", "date": "2023-05-01", "url": "https://x/BILLS-118hr1eh.htm"},
        {"type": "Public Law", "date": "2023-08-01", "url": "https://x/PLAW-118publ5.htm"},
    ]
    assert [version_id(v) for v in order_versions(rows)] == [
        "BILLS-118hr1ih",
        "BILLS-118hr1eh",
        "BILLS-118hr1enr",
        "PLAW-118publ5",
    ]


def test_actions_merge_duplicate_events():
    texts = [a["text"] for a in actions(BILL_ROW)]
    assert texts.count("Introduced in House") == 1 and len(texts) == 4


# -- fetching texts -------------------------------------------------------------------------


class FakeTransport:
    def __init__(self, versions):
        self.versions = versions
        self.calls = []

    def __call__(self, url, params, headers):
        self.calls.append((url, dict(params)))
        if url.startswith("https://api.congress.gov/"):
            assert params.get("api_key") == KEY
            body = {"textVersions": self.versions, "pagination": {"count": len(self.versions)}}
            return json.dumps(body).encode()
        assert "api_key" not in params and KEY not in url
        if url.endswith("BILLS-119hr7ih.htm"):
            return BILL_IH.encode()
        if url.endswith("BILLS-119hr7rh.htm"):
            return BILL_RH.encode()
        if url == "https://www.federalregister.gov/api/v1/documents/2025-09999.json":
            return json.dumps(
                {
                    "document_number": "2025-09999",
                    "publication_date": "2025-05-05",
                    "html_url": EO_ROW["html_url"],
                    "raw_text_url": "https://www.federalregister.gov/documents/full_text/text/"
                    "2025/05/05/2025-09999.txt",
                    "citation": "90 FR 100",
                    "executive_order_notes": "See: EO 14277",
                }
            ).encode()
        if url.endswith("/2025-09999.txt"):
            return EO_TEXT.encode()
        raise AssertionError(f"unexpected request {url}")


def _fetcher(cp, transport, max_queries=50):
    limiter = RateLimiter(0, sleep=lambda _s: None)
    client = CongressClient(KEY, cp.congress, transport=transport, limiter=limiter)
    return Fetcher(
        cp,
        client,
        max_queries=max_queries,
        transport=transport,
        document_limiter=limiter,
        fr_limiter=limiter,
    )


VERSIONS = [
    {
        "date": "2025-03-04T04:00:00Z",
        "type": "Reported in House",
        "formats": [
            {
                "type": "Formatted Text",
                "url": "https://www.congress.gov/119/bills/hr7/BILLS-119hr7rh.htm",
            }
        ],
    },
    {
        "date": "2025-02-03T05:00:00Z",
        "type": "Introduced in House",
        "formats": [
            {
                "type": "Formatted Text",
                "url": "https://www.congress.gov/119/bills/hr7/BILLS-119hr7ih.htm",
            }
        ],
    },
]


def _fetch(tree, cp, transport, **kw):
    cands = _candidates(tree, BILL_ROW, EO_ROW)
    pairs = [(k, cands[k]) for k in ("us-119-hr-7", "us-eo-14999")]
    return fetch_federal_texts(cp, _fetcher(cp, transport), pairs, **kw)


def test_fetch_texts_from_congress_gov_and_federal_register(tree, cp):
    transport = FakeTransport(VERSIONS)
    result = _fetch(tree, cp, transport, version="ai")
    assert result.errors == [] and sorted(result.fetched) == ["us-119-hr-7", "us-eo-14999"]
    store = TextStore(cp.texts)
    meta, text = store.for_key("us-119-hr-7")
    assert meta["doc_id"] == "BILLS-119hr7rh" and meta["type"] == "Reported in House"
    assert meta["url"] == "https://www.congress.gov/119/bills/hr7/BILLS-119hr7rh.htm"
    assert "instructional technology" in text
    # The latest version dropped AI, so the introduced text is stored beside it.
    emeta, etext = store.earlier_for_key("us-119-hr-7")
    assert emeta["doc_id"] == "BILLS-119hr7ih" and "artificial intelligence" in etext
    fmeta, ftext = store.for_key("us-eo-14999")
    assert fmeta["doc_id"] == "FR-2025-09999" and fmeta["url"] == EO_ROW["html_url"]
    assert "Secretary of Education shall issue guidance" in ftext
    # The API key never reaches the cache.
    for path in tree.cache.rglob("*"):
        if path.is_file():
            assert KEY not in path.read_text(encoding="utf-8", errors="ignore")
    # A second run is served from the cache (the version listing is reused for a day).
    again = FakeTransport(VERSIONS)
    second = _fetch(tree, cp, again, version="ai")
    assert again.calls == [] and sorted(second.cached) == ["us-119-hr-7", "us-eo-14999"]


def test_fetch_texts_respects_budget(tree, cp):
    transport = FakeTransport(VERSIONS)
    cands = _candidates(tree, BILL_ROW, EO_ROW)
    pairs = [(k, cands[k]) for k in ("us-119-hr-7", "us-eo-14999")]
    result = fetch_federal_texts(cp, _fetcher(cp, transport, max_queries=2), pairs)
    assert result.fetched == ["us-119-hr-7"] and result.skipped_budget == ["us-eo-14999"]
    assert len(transport.calls) == 2


def test_fetch_texts_without_versions(tree, cp):
    result = _fetch(tree, cp, FakeTransport([]))
    assert result.no_text == ["us-119-hr-7"]
    meta, text = TextStore(cp.texts).for_key("us-119-hr-7")
    assert meta["status"] == "text_unavailable" and text is None


# -- full stage, check, and import -------------------------------------------------------


def _triaged(cp, keys):
    out = cp.out_dir("triage")
    out.mkdir(parents=True, exist_ok=True)
    rows = [{"key": k, "decision": "needs_text", "reason": "Test."} for k in keys]
    (out / "batch-001.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )


def test_prep_full_federal_items(tree, cp):
    _fetch(tree, cp, FakeTransport(VERSIONS), version="ai")
    cands = _candidates(tree, BILL_ROW, EO_ROW)
    _triaged(cp, ["us-119-hr-7", "us-eo-14999"])
    result = prep_full(cp, cands, batch_size=20)
    assert result.items == 2
    items = {r["key"]: r for r in read_jsonl(result.batches[0])}
    bill = items["us-119-hr-7"]
    assert bill["text"]["status"] == "ok" and bill["text"]["earlier"]["type"].startswith("Intro")
    assert bill["triage"]["decision"] == "needs_text"
    assert [h["action"] for h in bill["history"]].count("Introduced in House") == 1
    eo = items["us-eo-14999"]
    assert eo["record_type"] == "executive-action" and eo["text"]["excerpts"]


def _judgments():
    bill = {
        "key": "us-119-hr-7",
        "in_scope": True,
        "confidence": "high",
        "education_levels": ["k12"],
        "topics": ["educator-training"],
        "summary": "Directs the Secretary of Education to award grants for teacher training on "
        "artificial intelligence. The AI provisions were removed by amendment.",
        "provisions": [
            {
                "type": "educator-pd-requirement",
                "description": "Grants fund professional development for teachers on AI.",
                "locator": "Introduced, Sec. 2(a)",
                "quote": "professional development for teachers on the use of artificial "
                "intelligence in the classroom",
            }
        ],
        "reasons": "Introduced Sec. 2(a) funds teacher professional development on AI.",
    }
    eo = {
        "key": "us-eo-14999",
        "in_scope": True,
        "confidence": "high",
        "education_levels": ["k12"],
        "topics": ["ai-literacy"],
        "summary": "Directs the Secretary of Education to issue guidance on AI in classrooms.",
        "provisions": [
            {
                "type": "issues-model-policy",
                "description": "The Secretary of Education must issue guidance on AI use.",
                "locator": "Sec. 2",
                "quote": "The Secretary of Education shall issue guidance to States on the use "
                "of artificial intelligence in classrooms",
            }
        ],
        "reasons": "Sec. 2 directs guidance on AI in classrooms.",
    }
    return bill, eo


def test_check_full_requires_document_classification(tree, cp):
    vocab = Vocab.load(tree.vocab)
    row = {
        "key": "us-fr-2025-08888",
        "in_scope": True,
        "confidence": "low",
        "education_levels": ["k12"],
        "topics": ["funding"],
        "summary": "Invites applications for grants on AI literacy in schools.",
        "provisions": [],
        "reasons": "The abstract invites AI literacy grant applications.",
    }
    store = TextStore(cp.texts)
    report = check_full([row], vocab, store, federal=True)
    assert any("classification is required" in e for e in report.items[0].errors)
    report = check_full([{**row, "classification": "program"}], vocab, store, federal=True)
    assert report.items[0].errors == []
    bill, _ = _judgments()
    report = check_full([{**bill, "classification": "program"}], vocab, store, federal=True)
    assert any("only for Federal Register" in e for e in report.items[0].errors)


def test_import_writes_valid_federal_records(tree, cp):
    _fetch(tree, cp, FakeTransport(VERSIONS), version="ai")
    cands = _candidates(tree, BILL_ROW, EO_ROW, NOTICE_ROW)
    bill, eo = _judgments()
    notice = {
        "key": "us-fr-2025-08888",
        "in_scope": True,
        "confidence": "low",
        "education_levels": ["k12"],
        "topics": ["funding"],
        "classification": "program",
        "summary": "Invites applications for grants on AI literacy in schools.",
        "provisions": [],
        "reasons": "The abstract invites AI literacy grant applications.",
    }
    vocab = Vocab.load(tree.vocab)
    report = check_full([bill, eo, notice], vocab, TextStore(cp.texts), federal=True)
    assert all(not c.errors for c in report.items)
    result = import_federal(cp, tree, report, cands, today=TODAY)
    assert result.rejected == [] and len(result.written) == 3
    rec = load_yaml(tree.data / "legislation/us/119/hr-7.yaml")
    assert rec["id"] == "us-119-hr-7" and rec["identifier"] == "H.R. 7"
    assert rec["external_ids"] == {"congress_gov": "119/hr/7"}
    assert rec["sources"][0]["url"] == BILL_ROW["url"]
    assert all("congress.gov" in s["url"] for s in rec["sources"])
    # The quote is only in the introduced text, so it cites that version.
    cite = rec["provisions"][0]["citation"]
    assert cite["source_url"].endswith("BILLS-119hr7ih.htm")
    assert [a["description"] for a in rec["actions"]].count("Introduced in House") == 1
    assert all(a["date"] <= TODAY.isoformat() for a in rec["actions"])
    assert [s["name"] for s in rec["sponsors"]] == [
        "Rep. Doe, Jane [D-CA-1]",
        "Sen. Roe, Rich [R-TX]",
    ]
    assert rec["sponsors"][1]["chamber"] == "upper"
    assert rec["verification"]["status"] == "machine-verified"
    order = load_yaml(tree.data / "executive-actions/us/eo-14999.yaml")
    assert order["issuer"] == "us-president" and order["number"] == "EO 14999"
    assert order["status"] == "active" and order["signed"] in ("2025-05-01", date(2025, 5, 1))
    assert order["provisions"][0]["citation"]["source_url"] == EO_ROW["html_url"]
    assert order["verification"]["status"] == "machine-verified"
    doc = load_yaml(tree.data / "documents/us/fr-2025-08888.yaml")
    assert doc["issuer"] == "us-ed" and doc["classification"] == "program"
    assert doc["sources"][0]["url"] == NOTICE_ROW["html_url"]
    assert doc["verification"]["status"] == "unverified"  # no text, no provisions
    vreport, _ = validate(Paths(tree.root), strict=True, today=TODAY)
    written = {"hr-7.yaml", "eo-14999.yaml", "fr-2025-08888.yaml"}
    assert [i.format() for i in vreport.issues if Path(i.file).name in written] == []
    # Records are never overwritten, and existing records are skipped.
    again = import_federal(
        cp, tree, report, _candidates(tree, BILL_ROW, EO_ROW, NOTICE_ROW), today=TODAY
    )
    assert again.written == [] and len(again.skipped) == 3


def test_eo_status_from_federal_register_notes():
    assert eo_status(None) == ("active", None)
    assert eo_status("See: EO 14110") == ("active", None)
    assert eo_status("Revoked by: EO 14148, January 20, 2025") == ("rescinded", "2025-01-20")
    assert eo_status("Superseded by EO 15000") == ("superseded", None)


def test_long_summary_is_cut_with_ai_passages():
    from edictai.classify.federal import SUMMARY_FULL_MAX, summary_view

    filler = "This title funds ships and aircraft for the Navy. " * 200
    summary = filler + "Sec. 9 requires a pilot on artificial intelligence training. " + filler
    view = summary_view(summary)
    assert len(view["description"]) <= SUMMARY_FULL_MAX
    assert any("artificial intelligence training" in p for p in view["description_ai_passages"])
    assert summary_view("Short summary.") == {"description": "Short summary."}
