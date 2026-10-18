import json
import zipfile
from datetime import date

import pytest

from statescope.ingest.bill_numbers import match_key
from statescope.ingest.candidates import (
    aligned_sessions,
    find_candidates,
    is_title_cased,
    match_keywords,
    trigger_strength,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Relating to artificial intelligence in schools", ["artificial intelligence"]),
        ("RELATING TO ARTIFICIAL INTELLIGENCE.", ["artificial intelligence"]),
        ("Creates the AI literacy program", ["AI"]),
        ("AI-generated content; disclosure", ["AI"]),
        ("Regulates deep fakes and deepfake media", ["deepfake"]),
        ("Companion chatbots; minors", ["chatbot"]),
        ("Algorithmic pricing", ["algorithm"]),
        ("Generative models and large language models", ["generative", "large language model"]),
        ("Machine-learning tools; synthetic media", ["machine learning", "synthetic media"]),
        # "AI" is case-sensitive and whole-word.
        ("Relating to ai. of the code; Hawaiian ai pono", []),
        ("Maintains the AID program; SAID; MAIL", []),
        ("Artificial insemination of livestock", []),
        # Title-cased titles (as LegiScan gives New Mexico's) may write "Ai".
        ("Ai Ethics As School Science Elective", ["AI"]),
        ("Relating to the Ai program", []),
    ],
)
def test_match_keywords(text, expected):
    assert match_keywords(text) == expected


def bill(bill_id, title, description="", history=None, state="TX"):
    return {
        "bill": {
            "bill_id": bill_id,
            "change_hash": f"ch{bill_id}",
            "state": state,
            "bill_number": f"HB{bill_id}",
            "bill_type": "B",
            "title": title,
            "description": description,
            "status": 1,
            "status_date": (history or [{"date": "2025-02-01"}])[-1]["date"],
            "state_link": f"https://capitol.example/HB{bill_id}",
            "url": f"https://legiscan.example/{bill_id}",
            "history": history
            or [{"date": "2025-02-01", "action": "Filed", "chamber": "H", "importance": 1}],
            "sponsors": [{"people_id": 7, "name": "A. Member", "party": "R", "role": "Rep"}],
            "texts": [
                {
                    "doc_id": 99,
                    "type": "Introduced",
                    "date": "2025-02-01",
                    "mime": "application/pdf",
                    "url": "https://legiscan.example/text/99",
                    "state_link": "https://capitol.example/HB1.pdf",
                }
            ],
        }
    }


def write_cache(root, sessions, archives):
    cache = root / "legiscan"
    (cache / "datasets").mkdir(parents=True)
    (cache / "sessions.json").write_text(json.dumps(sessions))
    for sid, bills in archives.items():
        with zipfile.ZipFile(cache / "datasets" / f"{sid}.zip", "w") as zf:
            for b in bills:
                zf.writestr(f"TX/2025/bill/HB{b['bill']['bill_id']}.json", json.dumps(b))
            zf.writestr("TX/2025/people/7.json", json.dumps({"person": {}}))
    return cache


SESSION = {
    "session_id": 2100,
    "state": "TX",
    "year_start": 2025,
    "year_end": 2025,
    "special": 0,
    "session_name": "89th Legislature Regular Session",
    "identifier": "2025",
}


def test_find_candidates(tmp_path):
    cache = write_cache(
        tmp_path,
        {
            "2100": SESSION,
            "1900": {**SESSION, "session_id": 1900, "year_start": 2021, "year_end": 2021},
            "2101": {**SESSION, "session_id": 2101},  # listed but not downloaded
        },
        {
            2100: [
                bill(1, "Relating to artificial intelligence"),
                bill(2, "Relating to school buses"),
                bill(3, "Schools", "Creates an AI advisory council"),
                bill(
                    4,
                    "Deepfake disclosure",
                    history=[
                        {"date": "2025-03-01", "action": "Filed", "chamber": "H"},
                        {"date": "2025-01-15", "action": "Prefiled", "chamber": "H"},
                    ],
                ),
            ],
            1900: [bill(5, "Relating to artificial intelligence")],
        },
    )
    out = tmp_path / "candidates"
    result = find_candidates(cache, out, date(2023, 1, 1), today=date(2026, 10, 17))
    assert result.path == out / "2026-10-17.jsonl"
    rows = [json.loads(line) for line in result.path.read_text().splitlines()]
    assert [r["bill_id"] for r in rows] == [1, 3, 4]
    assert result.total == 3 and result.bills_scanned == 4 and result.datasets == 1
    assert result.missing_datasets == [2101]
    first = rows[0]
    assert first["session"] == "2025"
    assert first["jurisdiction"] == "us-tx"
    assert first["url"] == "https://capitol.example/HB1"
    assert first["change_hash"] == "ch1"
    assert first["status_text"] == "Introduced"
    assert first["texts"][0]["doc_id"] == 99
    assert first["sponsors"][0]["name"] == "A. Member"
    assert "LegiScan" in first["attribution"]
    # History is put in date order.
    assert [h["date"] for h in rows[2]["history"]] == ["2025-01-15", "2025-03-01"]
    assert result.by_year == {2025: 3}
    assert result.by_keyword["AI"] == 1
    summary = json.loads((out / "2026-10-17.summary.json").read_text())
    assert summary["by_state"] == {"TX": 3}


def test_bills_without_activity_in_window_are_dropped(tmp_path):
    session = {**SESSION, "year_start": 2022, "year_end": 2023, "identifier": "2022-2023"}
    old = [{"date": "2022-03-01", "action": "Filed", "chamber": "H"}]
    new = [{"date": "2022-03-01", "action": "Filed"}, {"date": "2023-01-10", "action": "Heard"}]
    cache = write_cache(
        tmp_path,
        {"2100": session},
        {2100: [bill(1, "AI study", history=old), bill(2, "AI study", history=new)]},
    )
    result = find_candidates(cache, tmp_path / "c", date(2023, 1, 1), today=date(2026, 10, 17))
    assert result.total == 1
    assert result.by_year == {2022: 1}  # counted by the year the bill was filed


def test_requires_downloaded_index(tmp_path):
    with pytest.raises(FileNotFoundError, match="ingest legiscan"):
        find_candidates(tmp_path, tmp_path / "c", date(2023, 1, 1))


def test_is_title_cased():
    assert is_title_cased("Ai Ethics As School Science Elective")
    assert is_title_cased("Land Grant-merced & Acequia Infrastructure")
    assert not is_title_cased("RELATING TO ARTIFICIAL INTELLIGENCE.")
    assert not is_title_cased("Relating to school buses and other things")


def test_trigger_strength():
    assert trigger_strength(["algorithm"]) == "weak"
    assert trigger_strength(["algorithm", "AI"]) == "strong"
    assert trigger_strength([]) == "fulltext"


def test_fulltext_hits_and_existing_records(tmp_path):
    cache = write_cache(
        tmp_path,
        {"2100": SESSION},
        {
            2100: [
                bill(1, "Relating to artificial intelligence"),
                bill(2, "Relating to school safety"),  # found only by full text
                bill(3, "Relating to school buses"),
                bill(6, "Algorithmic rent pricing"),
            ]
        },
    )
    getbill = cache / "responses" / "getBill"
    getbill.mkdir(parents=True)
    late = bill(9, "Relating to schools")["bill"]
    late["session"] = {"session_id": 2100}
    (getbill / "9.json").write_text(json.dumps({"response": {"bill": late}}))
    (cache / "fulltext.json").write_text(
        json.dumps(
            {
                "hits": {
                    "1": {"queries": ["q1"]},
                    "2": {"queries": ["q1", "q2"]},
                    "9": {"queries": ["q2"]},
                    "404": {"queries": ["q2"]},
                }
            }
        )
    )
    existing = {("us-tx", "2025", match_key("us-tx", "H.B. 1")): "us-tx-2025-hb-1"}
    result = find_candidates(
        cache, tmp_path / "c", date(2023, 1, 1), today=date(2026, 10, 17), existing=existing
    )
    rows = {r["bill_id"]: r for r in map(json.loads, result.path.read_text().splitlines())}
    assert sorted(rows) == [1, 2, 6, 9]
    assert rows[1]["matched_by"] == "title" and rows[1]["fulltext_queries"] == ["q1"]
    assert rows[1]["existing_record"] == "us-tx-2025-hb-1"
    assert rows[1]["identifier"] == "HB 1"
    assert rows[2]["matched_by"] == "fulltext" and rows[2]["trigger_strength"] == "fulltext"
    assert rows[2]["existing_record"] is None
    assert rows[6]["trigger_strength"] == "weak"
    assert rows[9]["matched_by"] == "fulltext"  # from the getBill cache
    assert result.fulltext_unresolved == [404]
    assert result.existing == [("us-tx-2025-hb-1", 1)]
    assert result.by_matched == {"title": 2, "fulltext": 2}


def test_sessions_align_to_jurisdiction_files():
    sessions = {
        "1": {"session_id": 1, "state": "HI", "special": 0, "identifier": "2026"},
        "2": {"session_id": 2, "state": "HI", "special": 1, "identifier": "2026-s1"},
        "3": {"session_id": 3, "state": "TX", "special": 0, "identifier": "2025"},
    }
    listed = {"us-hi": ["2025-2026"], "us-tx": ["2025"]}
    aligned = aligned_sessions(sessions, listed)
    assert aligned["1"]["identifier"] == "2025-2026"
    assert aligned["1"]["legiscan_identifier"] == "2026"
    assert aligned["2"]["identifier"] == "2026-s1"
    assert aligned["3"]["identifier"] == "2025"
