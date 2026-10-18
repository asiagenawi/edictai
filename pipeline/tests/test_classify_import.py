from datetime import date

import pytest

from statescope.classify.actions import classify_text, map_history
from statescope.classify.common import ClassifyPaths
from statescope.classify.importer import (
    append_session,
    build_record,
    import_judgments,
    map_sponsors,
    map_versions,
    session_entry,
)
from statescope.classify.judgments import check_full
from statescope.classify.texts import TextStore
from statescope.paths import Paths
from statescope.status import derive_status
from statescope.validate import validate
from statescope.vocab import Vocab
from statescope.yamlio import load_yaml

from .conftest import make_tree

TODAY = date(2026, 10, 17)
TEXT = (
    "AN ACT relating to artificial intelligence in public schools.\n"
    "SECTION 1. Section 38.0231, Education Code, is added.\n"
    "(a) Each school district shall adopt a policy on the use of artificial intelligence by students.\n"
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Filed with the Clerk by Rep. Jane Doe", ["introduction"]),
        ("First Reading; Referred to Rules Committee", ["reading-1", "referral-committee"]),
        ("Introduced and referred to House Education", ["introduction", "referral-committee"]),
        (
            "Do Pass as Amended Executive; 007-003-000; Second Reading",
            ["reading-2", "committee-passage"],
        ),
        ("Third Reading - Short Debate - Passed 087-017-001", ["reading-3", "passage"]),
        ("Senate Committee Amendment No. 1 Adopted", ["amendment-passage"]),
        ("Amendment HA 1 to HS 1 - Introduced and Placed With Bill", ["other"]),
        ("Placed on Calendar Order of 3rd Reading", ["other"]),
        ("Read second time. Ordered to third reading.", ["reading-2"]),
        ("Passed First Reading.", ["reading-1"]),
        ("Senate amendments concurred in. To Engrossing and Enrolling.", ["passage"]),
        ("Motion to reconsider adopted", ["other"]),
        ("Sent to the Governor", ["executive-receipt"]),
        ("Signed by Officers and presented to Governor", ["executive-receipt"]),
        ("Governor Approved; Effective Date January 1, 2026", ["executive-signature"]),
        ("Chaptered by Secretary of State - Chapter 855, Statutes of 2026.", ["became-law"]),
        ("Public Act 104-0201", ["became-law"]),
        ("Vetoed by Governor", ["executive-veto"]),
        ("Senate sustained Governor's veto", ["other"]),
        ("Laid on Table; companion bill(s) passed, see CS/HB 1035", ["deferral"]),
        ("From printer. May be heard in committee March 23.", ["other"]),
        ("Notice of Intent to Act Published in the DC Register", ["other"]),
    ],
)
def test_classify_text(text, expected):
    assert classify_text(text) == expected


def test_president_means_senate_president_in_states():
    assert classify_text("Signed by President") == ["enrollment"]
    assert classify_text("Signed by President.", federal=True) == ["executive-signature"]


def test_map_history_uses_progress_without_inventing_actions():
    history = [
        {
            "date": "2025-01-14",
            "action": "Pending House Education",
            "chamber": "H",
            "importance": 0,
        },
        {
            "date": "2025-01-14",
            "action": "Read for the first time and referred to the House Committee on Education",
            "chamber": "H",
            "importance": 1,
        },
        {"date": "2025-03-01", "action": "Engrossed", "chamber": "H", "importance": 1},
    ]
    progress = [
        {"date": "2025-01-14", "event": 1},
        {"date": "2025-03-01", "event": 2},
        {"date": "2025-06-30", "event": 6},  # no history row that day: ignored
    ]
    actions = map_history(history, progress, jurisdiction="us-al")
    assert len(actions) == 3
    assert actions[1]["classification"] == ["reading-1", "referral-committee", "introduction"]
    assert actions[0]["classification"] == ["other"]
    assert actions[2]["classification"] == ["passage"]
    assert {a["actor"] for a in actions} == {"lower"}
    assert derive_status(actions, jurisdiction="us-al").status == "passed-chamber"


def test_map_history_actors():
    history = [
        {"date": "2025-01-02", "action": "Introduced", "chamber": "S"},
        {"date": "2025-05-02", "action": "Signed by the Governor", "chamber": "S"},
    ]
    assert [a["actor"] for a in map_history(history, jurisdiction="us-ga")] == [
        "upper",
        "executive",
    ]
    assert map_history(history[:1], jurisdiction="us-ne")[0]["actor"] == "legislature"


def test_map_sponsors_and_versions():
    sponsors = [
        {"name": "Jane Doe", "party": "R", "role": "Rep", "sponsor_type_id": 1, "sponsor_order": 1},
        {
            "name": "Education Committee",
            "role": "",
            "sponsor_type_id": 2,
            "sponsor_order": 3,
            "committee_sponsor": 1,
        },
        {"name": "John Roe", "party": "D", "role": "Sen", "sponsor_type_id": 2, "sponsor_order": 2},
    ]
    assert map_sponsors(sponsors, "us-tx") == [
        {"name": "Jane Doe", "primary": True, "party": "R", "chamber": "lower"},
        {"name": "John Roe", "primary": False, "party": "D", "chamber": "upper"},
        {"name": "Education Committee", "classification": "committee", "primary": False},
    ]
    texts = [
        {
            "doc_id": 2,
            "type": "Engrossed",
            "date": "0000-00-00",
            "mime": "application/pdf",
            "state_link": "https://lawfilesext.leg.wa.gov/x.pdf",
        },
        {
            "doc_id": 1,
            "type": "Introduced",
            "date": "2025-01-10",
            "mime": "text/html",
            "state_link": "https://legiscan.com/WA/text/x",
        },
        {"doc_id": 3, "type": "Enrolled", "date": "2025-04-01", "state_link": ""},
    ]
    assert map_versions(texts) == [
        {
            "note": "Engrossed",
            "url": "https://lawfilesext.leg.wa.gov/x.pdf",
            "media_type": "application/pdf",
        }
    ]


def candidate(**over):
    row = {
        "key": "us-tx-2025-hb-77",
        "bill_id": 9001,
        "jurisdiction": "us-tx",
        "session": "2025",
        "legiscan_session_id": 2160,
        "identifier": "HB 77",
        "bill_type": "B",
        "title": "Relating to artificial intelligence policies in public schools",
        "state_link": "https://capitol.texas.gov/BillLookup/History.aspx?LegSess=89R&Bill=HB77",
        "url": "https://capitol.texas.gov/BillLookup/History.aspx?LegSess=89R&Bill=HB77",
        "history": [
            {"date": "2025-02-01", "action": "Filed", "chamber": "H"},
            {"date": "2025-03-01", "action": "Referred to Public Education", "chamber": "H"},
        ],
        "sponsors": [{"name": "Jane Doe", "party": "R", "role": "Rep", "sponsor_type_id": 1}],
        "texts": [
            {
                "doc_id": 555,
                "type": "Introduced",
                "date": "2025-02-01",
                "mime": "text/html",
                "state_link": "https://capitol.texas.gov/tlodocs/89R/billtext/html/HB00077I.htm",
            }
        ],
        "existing_record": None,
    }
    row.update(over)
    return row


def judgment(**over):
    row = {
        "key": "us-tx-2025-hb-77",
        "in_scope": True,
        "confidence": "high",
        "education_levels": ["k12"],
        "topics": ["local-policy-requirement"],
        "summary": "Requires each school district to adopt a policy on student use of artificial intelligence.",
        "provisions": [
            {
                "type": "requires-local-ai-policy",
                "description": "Each district adopts a policy on student AI use.",
                "locator": "Sec. 1, Education Code 38.0231(a)",
                "quote": "Each school district shall adopt a policy on the use of artificial intelligence by students.",
            }
        ],
        "reasons": "Sec. 1 requires each district to adopt a student AI-use policy.",
    }
    row.update(over)
    return row


@pytest.fixture
def setup(tmp_path):
    paths = make_tree(tmp_path / "repo")
    cp = ClassifyPaths(paths)
    store = TextStore(cp.texts)
    store.store(
        "us-tx-2025-hb-77",
        {
            "doc_id": 555,
            "key": "us-tx-2025-hb-77",
            "status": "ok",
            "type": "Introduced",
            "state_link": "https://capitol.texas.gov/tlodocs/89R/billtext/html/HB00077I.htm",
        },
        TEXT,
    )
    store.save_index()
    return paths, cp


def test_build_record_machine_verified(setup):
    _paths, cp = setup
    built = build_record(judgment(), candidate(), None, TextStore(cp.texts), today=TODAY)
    rec = built.record
    assert rec["id"] == "us-tx-2025-hb-77"
    assert str(built.path) == "legislation/us-tx/2025/hb-77.yaml"
    assert rec["verification"]["status"] == "machine-verified"
    assert rec["verification"]["method"] == "legiscan-text+classifier"
    assert [s["url"] for s in rec["sources"]] == [
        "https://capitol.texas.gov/BillLookup/History.aspx?LegSess=89R&Bill=HB77",
        "https://capitol.texas.gov/tlodocs/89R/billtext/html/HB00077I.htm",
    ]
    assert all(s["kind"] == "official" and s["accessed"] == "2026-10-17" for s in rec["sources"])
    assert rec["external_ids"] == {"legiscan_bill_id": 9001}
    assert rec["actions"][0]["classification"] == ["introduction"]
    assert rec["provisions"][0]["citation"]["source_url"].endswith("HB00077I.htm")
    assert "legiscan.com" not in str(rec["versions"])


def test_build_record_unverified_quote_dropped(setup):
    _paths, cp = setup
    bad = judgment()
    bad["provisions"][0]["quote"] = "Each school district must adopt a strict AI policy."
    report = check_full([bad], Vocab.load(cp.paths.vocab), TextStore(cp.texts))
    built = build_record(
        bad,
        candidate(),
        None,
        TextStore(cp.texts),
        today=TODAY,
        quote_failures=report.items[0].quote_failures,
    )
    assert "provisions" not in built.record
    assert built.record["verification"]["status"] == "unverified"
    assert "quote not verified" in built.record["verification"]["notes"]


def test_build_record_cites_earlier_version_for_amended_out_quotes(setup):
    _paths, cp = setup
    store = TextStore(cp.texts)
    key = "us-tx-2025-hb-77"
    store.store(
        key,
        {
            "doc_id": 556,
            "key": key,
            "status": "ok",
            "type": "Enrolled",
            "state_link": "https://capitol.texas.gov/tlodocs/89R/billtext/html/HB00077F.htm",
        },
        "AN ACT relating to public schools.\nSECTION 1. Each school district shall report.\n",
    )
    store.store_earlier(
        key,
        {
            "doc_id": 555,
            "key": key,
            "status": "ok",
            "type": "Introduced",
            "state_link": "https://capitol.texas.gov/tlodocs/89R/billtext/html/HB00077I.htm",
        },
        TEXT,
    )
    store.save_index()
    row = judgment()
    row["provisions"][0]["locator"] = "Introduced, Sec. 1"
    report = check_full([row], Vocab.load(cp.paths.vocab), TextStore(cp.texts))
    assert report.ok, report.lines()
    built = build_record(row, candidate(), None, TextStore(cp.texts), today=TODAY)
    rec = built.record
    cite = rec["provisions"][0]["citation"]
    assert cite["source_url"].endswith("HB00077I.htm") and cite["locator"] == "Introduced, Sec. 1"
    assert [s["title"] for s in rec["sources"]] == [
        "HB 77 bill page",
        "HB 77 text (Enrolled)",
        "HB 77 text (Introduced)",
    ]
    assert rec["verification"]["status"] == "machine-verified"
    assert "earlier version (Introduced)" in rec["verification"]["notes"]


def test_build_record_needs_official_url(setup):
    _paths, cp = setup
    row = candidate(state_link="https://legiscan.com/TX/bill/HB77/2025", url=None, texts=[])
    built = build_record(
        judgment(key="us-tx-2025-hb-78"), row, None, TextStore(cp.texts), today=TODAY
    )
    assert built.record is None and "official URL" in built.problems[0]


def test_import_writes_valid_records_and_sessions(setup):
    paths, cp = setup
    other = candidate(
        key="us-tx-2026-hb-5",
        identifier="HB 5",
        session="2026",
        bill_id=9002,
        legiscan_session_id=3000,
        history=[{"date": "2026-01-20", "action": "Filed", "chamber": "H"}],
    )
    j2 = judgment(key="us-tx-2026-hb-5", provisions=[])
    out = judgment(key="us-tx-2025-hb-99", in_scope=False, confidence="high", reasons="Insurance.")
    for k in ("education_levels", "topics", "summary", "provisions"):
        out.pop(k)
    report = check_full([judgment(), j2, out], Vocab.load(paths.vocab), TextStore(cp.texts))
    sessions = {
        "3000": {
            "session_id": 3000,
            "year_start": 2026,
            "year_end": 2026,
            "special": 0,
            "session_name": "2026 Regular Session",
        }
    }
    result = import_judgments(
        cp,
        paths,
        report,
        {"us-tx-2025-hb-77": candidate(), "us-tx-2026-hb-5": other},
        sessions,
        today=TODAY,
    )
    assert result.out_of_scope == 1
    assert [p.name for p in result.written] == ["hb-77.yaml", "hb-5.yaml"]
    assert result.machine_verified == 1 and result.unverified == 1
    # No dataset is cached for session 3000, so no start date: the session is not invented.
    assert result.sessions_added == []
    rec = load_yaml(paths.data / "legislation/us-tx/2025/hb-77.yaml")
    assert rec["session"] == "2025" and rec["actions"][0]["date"] == "2025-02-01"
    report_v, _ = validate(Paths(paths.root), strict=True, today=TODAY)
    hb77 = [i for i in report_v.issues if "hb-77" in i.file]
    assert hb77 == []
    # Existing files are not overwritten.
    again = import_judgments(
        cp, paths, report, {"us-tx-2025-hb-77": candidate()}, sessions, today=TODAY
    )
    assert again.written == [] and "already recorded" in again.skipped[0][1]


def test_append_session_keeps_other_lines():
    text = (
        'id: us-tx\nname: Texas\nsessions:\n  - identifier: "2025"\n    name: 89th\n'
        "    start_date: 2025-01-14\nregion: Southwest\n"
    )
    entry = {
        "identifier": "2026",
        "name": "2026 Regular Session",
        "classification": "primary",
        "start_date": "2026-01-12",
        "legiscan_session_id": 3000,
    }
    out = append_session(text, entry)
    assert out.startswith(text.split("region:")[0])
    assert '  - identifier: "2026"\n    name: 2026 Regular Session\n' in out
    assert out.rstrip().endswith("region: Southwest")
    assert "sessions:\n" in append_session("id: us-xx\n", entry)


class FakeOpenStates:
    def __init__(self, sessions):
        self.sessions = sessions

    def jurisdiction(self, jurisdiction):
        return {"name": "Texas", "legislative_sessions": self.sessions}

    def accessed(self, jurisdiction):
        return "2026-10-01"


class OneBillStore:
    def __init__(self, dates):
        self.dates = dates

    def session_bills(self, session_id):
        return [{"history": [{"date": d} for d in self.dates]}]


LS_SESSION = {
    "session_id": 2160,
    "year_start": 2025,
    "special": 0,
    "session_name": "89th Legislature",
}


def test_session_entry_prefers_open_states_dates():
    os_ = FakeOpenStates(
        [
            {
                "identifier": "89",
                "name": "89th",
                "classification": "primary",
                "start_date": "2025-01-14",
                "end_date": "2025-06-02",
            }
        ]
    )
    entry = session_entry(
        {**LS_SESSION},
        OneBillStore(["2024-11-12", "2025-01-02", "2025-05-30"]),
        "2025",
        jurisdiction="us-tx",
        openstates=os_,
        today=TODAY,
    )
    # Matched on the span of years; convening date, not the first filing.
    assert entry["start_date"] == "2025-01-14"
    assert entry["end_date"] == "2025-06-02"
    assert entry["sources"][0]["publisher"] == "Open States"
    assert entry["sources"][0]["accessed"] == "2026-10-01"
    assert list(entry)[-1] == "sources"


def test_session_entry_never_writes_future_end():
    os_ = FakeOpenStates(
        [
            {
                "identifier": "2025",
                "classification": "primary",
                "start_date": "2025-01-14",
                "end_date": "2027-01-01",
            }
        ]
    )
    entry = session_entry(
        LS_SESSION,
        OneBillStore(["2025-02-01"]),
        "2025",
        jurisdiction="us-tx",
        openstates=os_,
        today=TODAY,
    )
    assert entry["start_date"] == "2025-01-14" and "end_date" not in entry


def test_session_entry_falls_back_to_earliest_action():
    entry = session_entry(
        LS_SESSION,
        OneBillStore(["2024-11-12", "2025-01-02"]),
        "2025",
        jurisdiction="us-tx",
        openstates=FakeOpenStates([]),
        today=TODAY,
    )
    assert entry["start_date"] == "2025-01-02"
    assert "end_date" not in entry and "sources" not in entry


def test_append_session_writes_dates_and_source():
    entry = {
        "identifier": "2025",
        "name": "89th Legislature",
        "classification": "primary",
        "start_date": "2025-01-14",
        "end_date": "2025-06-02",
        "legiscan_session_id": 2160,
        "sources": [
            {
                "url": "https://v3.openstates.org/jurisdictions/x",
                "title": "Open States API, Texas legislative sessions",
                "publisher": "Open States",
                "kind": "secondary",
                "accessed": "2026-10-01",
            }
        ],
    }
    out = append_session("id: us-tx\nsessions:\n", entry)
    import yaml

    data = yaml.safe_load(out)
    s = data["sessions"][0]
    assert s["end_date"] == "2025-06-02" and s["sources"][0]["kind"] == "secondary"
