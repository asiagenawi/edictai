"""Find AI-related bills in the cached LegiScan datasets.

Runs entirely on local files: no API calls. A bill is a candidate when its
title or description matches one of the keyword patterns below
(`matched_by: title`), or when one of the full-text searches in
`fulltext.py` found it (`matched_by: fulltext`). The classifier decides scope
later; `trigger_strength` lets it triage matches on weak keywords alone.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .bill_numbers import match_key, normalize
from .legiscan import ATTRIBUTION
from .session_ids import align

# Everything is case-insensitive except "AI", which must be upper-case so that
# words like Hawaiian "ai" and code paragraph designators ("ai.") do not
# match. In a title-cased text ("Ai Ethics As School Science Elective"),
# "Ai" counts too.
KEYWORDS: dict[str, re.Pattern[str]] = {
    "artificial intelligence": re.compile(r"\bartificial[\s-]+intelligence\b", re.IGNORECASE),
    "machine learning": re.compile(r"\bmachine[\s-]+learning\b", re.IGNORECASE),
    "generative": re.compile(r"\bgenerative\b", re.IGNORECASE),
    "deepfake": re.compile(r"\bdeep[\s-]?fakes?\b", re.IGNORECASE),
    "synthetic media": re.compile(r"\bsynthetic\s+media\b", re.IGNORECASE),
    "chatbot": re.compile(r"\bchat[\s-]?bots?\b", re.IGNORECASE),
    "algorithm": re.compile(r"\balgorithm(?:s|ic|ically)?\b", re.IGNORECASE),
    "large language model": re.compile(r"\blarge[\s-]+language[\s-]+models?\b", re.IGNORECASE),
    "AI": re.compile(r"(?<![A-Za-z0-9])AI(?![A-Za-z0-9])"),
}

# Keywords common outside AI contexts (rental-pricing and other algorithm bills).
WEAK_KEYWORDS = frozenset({"algorithm"})
_TITLE_CASE_AI = re.compile(r"(?<![A-Za-z0-9])A[Ii](?![A-Za-z0-9])")

# LegiScan status codes for the bill's current progress step.
STATUS_TEXT = {
    0: "N/A",
    1: "Introduced",
    2: "Engrossed",
    3: "Enrolled",
    4: "Passed",
    5: "Vetoed",
    6: "Failed",
}


def is_title_cased(text: str) -> bool:
    """Most words of four or more letters start with a capital, and not all are capitals."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z']*", text) if len(w) > 3]
    if len(words) < 2 or all(w.isupper() for w in words):
        return False
    return sum(w[0].isupper() for w in words) / len(words) >= 0.75


def match_keywords(*texts: str | None) -> list[str]:
    present = [t for t in texts if t]
    joined = "\n".join(present)
    found = [name for name, pattern in KEYWORDS.items() if pattern.search(joined)]
    if "AI" not in found and any(is_title_cased(t) and _TITLE_CASE_AI.search(t) for t in present):
        found.append("AI")
    return found


def trigger_strength(keywords: list[str]) -> str:
    """'strong' with any AI-specific keyword, 'weak' with weak ones only, else 'fulltext'."""
    if not keywords:
        return "fulltext"
    return "weak" if set(keywords) <= WEAK_KEYWORDS else "strong"


def iter_bills(archive: Path) -> Iterator[dict[str, Any]]:
    """The `bill` objects in one dataset zip."""
    with zipfile.ZipFile(archive) as zf:
        for name in sorted(zf.namelist()):
            if "/bill/" not in name or not name.endswith(".json"):
                continue
            data = json.loads(zf.read(name))
            bill = data.get("bill") if isinstance(data, dict) else None
            if isinstance(bill, dict):
                yield bill


def _dates(bill: Mapping[str, Any]) -> list[str]:
    found = [h.get("date") for h in bill.get("history") or [] if isinstance(h, dict)]
    found.append(bill.get("status_date"))
    return sorted(d for d in found if isinstance(d, str) and len(d) == 10)


def jurisdiction_of(state: str | None) -> str:
    return "us" if state == "US" else f"us-{(state or '').lower()}"


def candidate(
    bill: Mapping[str, Any],
    session: Mapping[str, Any],
    keywords: list[str],
    fulltext_queries: list[str] | None = None,
    existing_record: str | None = None,
) -> dict[str, Any]:
    status = bill.get("status")
    jurisdiction = jurisdiction_of(bill.get("state"))
    history = sorted(
        (
            {"date": h.get("date"), "action": h.get("action"), "chamber": h.get("chamber")}
            for h in bill.get("history") or []
        ),
        key=lambda h: h["date"] or "",
    )
    return {
        "bill_id": bill.get("bill_id"),
        "state": bill.get("state"),
        "jurisdiction": jurisdiction,
        "session": session["identifier"],
        "legiscan_session_id": session["session_id"],
        "session_name": session.get("session_name"),
        "bill_number": bill.get("bill_number"),
        "identifier": normalize(jurisdiction, str(bill.get("bill_number") or "")),
        "bill_type": bill.get("bill_type"),
        "title": bill.get("title"),
        "description": bill.get("description"),
        "status": status,
        "status_text": STATUS_TEXT.get(status) if isinstance(status, int) else None,
        "status_date": bill.get("status_date"),
        "history": history,
        "sponsors": [
            {
                "people_id": s.get("people_id"),
                "name": s.get("name"),
                "party": s.get("party"),
                "role": s.get("role"),
                "sponsor_type_id": s.get("sponsor_type_id"),
                "sponsor_order": s.get("sponsor_order"),
            }
            for s in bill.get("sponsors") or []
        ],
        "texts": [
            {
                "doc_id": t.get("doc_id"),
                "type": t.get("type"),
                "date": t.get("date"),
                "mime": t.get("mime"),
                "url": t.get("url"),
                "state_link": t.get("state_link"),
            }
            for t in bill.get("texts") or []
        ],
        "url": bill.get("state_link"),
        "legiscan_url": bill.get("url"),
        "change_hash": bill.get("change_hash"),
        "matched_by": "title" if keywords else "fulltext",
        "matched_keywords": keywords,
        "trigger_strength": trigger_strength(keywords),
        "fulltext_queries": fulltext_queries or [],
        "existing_record": existing_record,
        "attribution": ATTRIBUTION,
    }


def bill_year(row: Mapping[str, Any], session: Mapping[str, Any]) -> int:
    dates = [h["date"] for h in row["history"] if h.get("date")]
    if dates:
        return int(min(dates)[:4])
    if row.get("status_date"):
        return int(row["status_date"][:4])
    return int(session["year_start"])


@dataclass
class CandidateResult:
    path: Path
    total: int = 0
    bills_scanned: int = 0
    datasets: int = 0
    by_state: Counter[str] = field(default_factory=Counter)
    by_year: Counter[int] = field(default_factory=Counter)
    by_state_year: Counter[tuple[str, int]] = field(default_factory=Counter)
    by_keyword: Counter[str] = field(default_factory=Counter)
    by_matched: Counter[str] = field(default_factory=Counter)
    by_strength: Counter[str] = field(default_factory=Counter)
    existing: list[tuple[str, int]] = field(default_factory=list)  # (record id, bill_id)
    missing_datasets: list[int] = field(default_factory=list)
    fulltext_unresolved: list[int] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "bills_scanned": self.bills_scanned,
            "datasets": self.datasets,
            "by_year": {str(k): v for k, v in sorted(self.by_year.items())},
            "by_state": dict(self.by_state.most_common()),
            "by_state_year": {f"{s} {y}": n for (s, y), n in sorted(self.by_state_year.items())},
            "by_keyword": dict(self.by_keyword.most_common()),
            "by_matched_by": dict(self.by_matched.most_common()),
            "by_trigger_strength": dict(self.by_strength.most_common()),
            "existing_records": len(self.existing),
        }


def existing_records(data_dir: Path) -> dict[tuple[str, str, tuple[str, int]], str]:
    """(jurisdiction, session, bill-number key) -> id of each data/legislation record."""
    from ..records import load_records

    index: dict[tuple[str, str, tuple[str, int]], str] = {}
    for record in load_records(data_dir, ["legislation"]):
        d = record.data
        key = match_key(str(d.get("jurisdiction")), str(d.get("identifier") or ""))
        if key and record.id:
            index[(str(d.get("jurisdiction")), str(d.get("session")), key)] = record.id
    return index


def listed_sessions(data_dir: Path) -> dict[str, list[str]]:
    """Jurisdiction id -> session identifiers listed in data/jurisdictions."""
    from ..records import load_records

    return {
        str(r.id): [str(s.get("identifier")) for s in r.data.get("sessions") or []]
        for r in load_records(data_dir, ["jurisdiction"])
    }


def aligned_sessions(
    sessions: Mapping[str, Mapping[str, Any]], listed: Mapping[str, list[str]]
) -> dict[str, dict[str, Any]]:
    """The LegiScan session index with identifiers aligned to data/jurisdictions."""
    out: dict[str, dict[str, Any]] = {}
    for sid, s in sessions.items():
        row = dict(s)
        if not s.get("special"):
            jur = jurisdiction_of(str(s.get("state")))
            row["identifier"] = align(str(s["identifier"]), listed.get(jur, []))
        row["legiscan_identifier"] = s["identifier"]
        out[sid] = row
    return out


def _fulltext_hits(legiscan_cache: Path) -> dict[int, list[str]]:
    path = legiscan_cache / "fulltext.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): v["queries"] for k, v in data.get("hits", {}).items()}


def find_candidates(
    legiscan_cache: Path,
    out_dir: Path,
    since: date,
    *,
    today: date | None = None,
    existing: Mapping[tuple[str, str, tuple[str, int]], str] | None = None,
    listed: Mapping[str, list[str]] | None = None,
) -> CandidateResult:
    today = today or date.today()
    existing = existing or {}
    index_path = legiscan_cache / "sessions.json"
    if not index_path.exists():
        raise FileNotFoundError(f"{index_path} not found; run `edictai ingest legiscan` first")
    sessions = aligned_sessions(json.loads(index_path.read_text(encoding="utf-8")), listed or {})
    hits = _fulltext_hits(legiscan_cache)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = CandidateResult(path=out_dir / f"{today.isoformat()}.jsonl")
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()

    def consider(bill: Mapping[str, Any], session: Mapping[str, Any]) -> None:
        seen.add(int(bill.get("bill_id") or 0))
        keywords = match_keywords(bill.get("title"), bill.get("description"))
        queries = hits.get(int(bill.get("bill_id") or 0))
        if not keywords and not queries:
            return
        dates = _dates(bill)
        if dates and dates[-1] < since.isoformat():
            return  # no activity in the window
        jurisdiction = jurisdiction_of(bill.get("state"))
        key = match_key(jurisdiction, normalize(jurisdiction, str(bill.get("bill_number") or "")))
        record_id = existing.get((jurisdiction, session["identifier"], key)) if key else None
        row = candidate(bill, session, keywords, queries, record_id)
        rows.append(row)
        year = bill_year(row, session)
        result.by_state[row["state"]] += 1
        result.by_year[year] += 1
        result.by_state_year[(row["state"], year)] += 1
        result.by_keyword.update(keywords)
        result.by_matched[row["matched_by"]] += 1
        result.by_strength[row["trigger_strength"]] += 1
        if record_id:
            result.existing.append((record_id, int(row["bill_id"])))

    for sid, session in sorted(sessions.items(), key=lambda kv: int(kv[0])):
        end = int(session.get("year_end") or session["year_start"])
        if end < since.year or int(session["year_start"]) > today.year:
            continue
        archive = legiscan_cache / "datasets" / f"{sid}.zip"
        if not archive.exists():
            result.missing_datasets.append(int(sid))
            continue
        result.datasets += 1
        for bill in iter_bills(archive):
            result.bills_scanned += 1
            consider(bill, session)

    # Full-text hits missing from the datasets were fetched with getBill.
    for bill_id in sorted(set(hits) - seen):
        path = legiscan_cache / "responses" / "getBill" / f"{bill_id}.json"
        if not path.exists():
            result.fulltext_unresolved.append(bill_id)
            continue
        bill = json.loads(path.read_text(encoding="utf-8"))["response"]["bill"]
        session = sessions.get(str((bill.get("session") or {}).get("session_id")))
        if session is None:
            result.fulltext_unresolved.append(bill_id)
            continue
        consider(bill, session)

    rows.sort(key=lambda r: (r["state"] or "", r["session"], r["bill_id"] or 0))
    tmp = result.path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(result.path)
    result.total = len(rows)
    summary_path = result.path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(result.summary(), indent=1) + "\n", encoding="utf-8")
    return result
