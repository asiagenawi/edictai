"""Run the federal searches and write candidate rows.

Output: `.cache/candidates/federal-<date>.jsonl`, one JSON object per line,
plus `federal-<date>.summary.json` with the counts. Each row carries
`existing` (bool) and `existing_record` (the matching record id, or null).
Nothing under `data/` is written.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ..ingest.candidates import match_keywords
from .congress import CongressClient, CongressError, bill_key, normalize_bill, strip_html
from .existing import ExistingIndex, build_index
from .federal_register import (
    FederalRegisterClient,
    Search,
    agency_documents,
    executive_orders,
    normalize_document,
    presidential_memoranda,
)

CONGRESSES = (118, 119)
# Stop the titles pass when the API keeps failing rather than log every bill.
MAX_CONSECUTIVE_TITLE_ERRORS = 25
ED_AGENCY = "education-department"

Progress = Callable[[str], None]


@dataclass
class _Hit:
    matched_in: set[str] = field(default_factory=set)
    update_date: str | None = None


@dataclass
class FederalResult:
    path: Path
    bills: int = 0
    documents: int = 0
    bills_scanned: Counter[int] = field(default_factory=Counter)
    summaries_scanned: Counter[int] = field(default_factory=Counter)
    titles_checked: Counter[int] = field(default_factory=Counter)
    # Bills whose titles were not fetched because the run's title requests ran out.
    titles_deferred: Counter[int] = field(default_factory=Counter)
    # Title requests this run may still send (None: no limit).
    title_requests_left: int | None = None
    bills_by_congress: Counter[int] = field(default_factory=Counter)
    bills_by_type: Counter[str] = field(default_factory=Counter)
    bills_by_match: Counter[str] = field(default_factory=Counter)
    documents_by_search: Counter[str] = field(default_factory=Counter)
    documents_by_type: Counter[str] = field(default_factory=Counter)
    existing: list[str] = field(default_factory=list)
    missing_existing: list[str] = field(default_factory=list)
    congress_queries: int = 0
    congress_cache_hits: int = 0
    fr_queries: int = 0
    fr_cache_hits: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "bills": self.bills,
            "documents": self.documents,
            "bills_scanned": {str(k): v for k, v in sorted(self.bills_scanned.items())},
            "summaries_scanned": {str(k): v for k, v in sorted(self.summaries_scanned.items())},
            "titles_checked": {str(k): v for k, v in sorted(self.titles_checked.items())},
            "titles_deferred": {str(k): v for k, v in sorted(self.titles_deferred.items())},
            "bills_by_congress": {str(k): v for k, v in sorted(self.bills_by_congress.items())},
            "bills_by_type": dict(self.bills_by_type.most_common()),
            "bills_by_match": dict(self.bills_by_match.most_common()),
            "documents_by_search": dict(self.documents_by_search.most_common()),
            "documents_by_type": dict(self.documents_by_type.most_common()),
            "existing": sorted(self.existing),
            "existing_not_found": sorted(self.missing_existing),
            "queries": {
                "congress": self.congress_queries,
                "congress_cache_hits": self.congress_cache_hits,
                "federal_register": self.fr_queries,
                "federal_register_cache_hits": self.fr_cache_hits,
            },
            "errors": self.errors,
        }


def _latest(*dates: str | None) -> str | None:
    found = [d for d in dates if d]
    return max(found) if found else None


def discover_bills(
    client: CongressClient,
    congress: int,
    since: date,
    index: ExistingIndex,
    result: FederalResult,
    progress: Progress,
    *,
    titles: bool = False,
) -> dict[tuple[int, str, str], _Hit]:
    """Bills whose display title, CRS summary, or (with `titles`) any title matches a keyword,
    plus known records."""
    hits: dict[tuple[int, str, str], _Hit] = {}
    listed: list[tuple[tuple[int, str, str], str | None]] = []

    def hit(key: tuple[int, str, str], how: str, update: str | None) -> None:
        h = hits.setdefault(key, _Hit())
        h.matched_in.add(how)
        h.update_date = _latest(h.update_date, update)

    for i, bill in enumerate(client.bills(congress), 1):
        result.bills_scanned[congress] += 1
        if i % 5000 == 0:
            progress(f"congress {congress}: {i} bills scanned")
        key = bill_key(congress, bill["type"], bill["number"])
        update = bill.get("updateDateIncludingText") or bill.get("updateDate")
        if match_keywords(bill.get("title")):
            hit(key, "title", update)
        elif titles:
            listed.append((key, update))
    since_dt = datetime(since.year, since.month, since.day, tzinfo=UTC)
    for i, summary in enumerate(client.summaries(congress, since_dt), 1):
        result.summaries_scanned[congress] += 1
        if i % 5000 == 0:
            progress(f"congress {congress}: {i} summaries scanned")
        bill = summary.get("bill") or {}
        if not bill.get("type") or not bill.get("number"):
            continue
        if match_keywords(strip_html(summary.get("text")), bill.get("title")):
            hit(
                bill_key(congress, bill["type"], bill["number"]),
                "summary",
                bill.get("updateDateIncludingText"),
            )
    if titles:
        progress(f"congress {congress}: checking titles of {len(listed)} bills")
        failures = 0
        for i, (key, update) in enumerate(listed, 1):
            if i % 1000 == 0:
                progress(f"congress {congress}: {i}/{len(listed)} bills' titles checked")
            if result.title_requests_left is not None and not client.titles_cached(*key, update):
                if result.title_requests_left <= 0:
                    result.titles_deferred[congress] += 1
                    continue
                result.title_requests_left -= 1
            try:
                found = client.bill_titles(*key, update)
            except CongressError as exc:
                result.errors.append(f"{key[0]}/{key[1]}/{key[2]} titles: {exc}")
                failures += 1
                if failures >= MAX_CONSECUTIVE_TITLE_ERRORS:
                    result.errors.append(f"congress {congress}: titles pass stopped after errors")
                    break
                continue
            failures = 0
            result.titles_checked[congress] += 1
            if match_keywords(*found):
                hit(key, "titles", update)
    for key in index.bills:
        if key[0] == congress:
            hit(key, "existing", None)
    return hits


def bill_rows(
    client: CongressClient,
    congresses: Iterable[int],
    since: date,
    index: ExistingIndex,
    result: FederalResult,
    progress: Progress,
    *,
    titles: bool = False,
) -> list[dict[str, Any]]:
    rows = []
    for congress in congresses:
        hits = discover_bills(client, congress, since, index, result, progress, titles=titles)
        progress(f"congress {congress}: {len(hits)} bills to fetch")
        for i, (key, h) in enumerate(sorted(hits.items(), key=lambda kv: _sort_key(kv[0])), 1):
            if i % 50 == 0:
                progress(f"congress {congress}: {i}/{len(hits)} bill details")
            try:
                details = client.bill_details(*key, update_date=h.update_date)
            except Exception as exc:
                result.errors.append(f"{key[0]}/{key[1]}/{key[2]}: {exc}")
                continue
            if not details.get("bill"):
                result.errors.append(f"{key[0]}/{key[1]}/{key[2]}: no bill in response")
                continue
            row = normalize_bill(details)
            row["matched_in"] = sorted(h.matched_in)
            row["matched_keywords"] = match_keywords(row["title"], *row["titles"], row["summary"])
            rid = index.match_bill(*key)
            row["existing"] = rid is not None
            row["existing_record"] = rid
            rows.append(row)
            result.bills_by_congress[congress] += 1
            result.bills_by_type[row["type"]] += 1
            result.bills_by_match.update(row["matched_in"])
            if rid:
                result.existing.append(rid)
    return rows


def default_searches(since: date) -> list[Search]:
    return [
        executive_orders(since),
        presidential_memoranda(since),
        agency_documents(ED_AGENCY, since),
    ]


def document_rows(
    client: FederalRegisterClient,
    searches: Iterable[Search],
    index: ExistingIndex,
    result: FederalResult,
) -> list[dict[str, Any]]:
    by_number: dict[str, dict[str, Any]] = {}
    for search in searches:
        for doc in client.search(search):
            number = doc.get("document_number")
            if not number:
                continue
            result.documents_by_search[search.name] += 1
            if number in by_number:
                if search.name not in by_number[number]["searches"]:
                    by_number[number]["searches"].append(search.name)
                continue
            row = normalize_document(doc, search.name)
            row["searches"] = [row.pop("search")]
            rid = index.match_document(number, row["executive_order_number"])
            row["existing"] = rid is not None
            row["existing_record"] = rid
            by_number[number] = row
    rows = sorted(
        by_number.values(), key=lambda r: (r["publication_date"] or "", r["document_number"])
    )
    for row in rows:
        if row["executive_order_number"]:
            kind = "executive-order"
        elif "presidential-memoranda" in row["searches"]:
            kind = "presidential-memorandum"
        else:
            kind = row["type"] or "unknown"
        result.documents_by_type[kind] += 1
        if row["existing_record"]:
            result.existing.append(row["existing_record"])
    return rows


def _sort_key(key: tuple[int, str, str]) -> tuple[int, str, int, str]:
    congress, bill_type, number = key
    return congress, bill_type, int(number) if number.isdigit() else 0, number


def find_federal_candidates(
    data_dir: Path,
    out_dir: Path,
    *,
    congress: CongressClient | None,
    federal_register: FederalRegisterClient | None,
    congresses: Iterable[int] = CONGRESSES,
    since: date = date(2023, 1, 1),
    searches: Iterable[Search] | None = None,
    today: date | None = None,
    progress: Progress | None = None,
    titles: bool = False,
    title_requests: int | None = None,
) -> FederalResult:
    """Search both sources, flag known items, and write the candidate file.

    Pass `congress=None` or `federal_register=None` to skip that source, and
    `titles=True` to also match every listed bill's titles (one request per bill).
    `title_requests` caps the uncached title requests; bills past it are checked
    on a later run, since cached titles are reused.
    """
    today = today or date.today()
    progress = progress or (lambda _m: None)
    index = build_index(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = FederalResult(path=out_dir / f"federal-{today.isoformat()}.jsonl")
    result.title_requests_left = title_requests
    rows: list[dict[str, Any]] = []
    congresses = tuple(congresses)
    if congress is not None:
        rows += bill_rows(congress, congresses, since, index, result, progress, titles=titles)
        result.congress_queries = congress.queries
        result.congress_cache_hits = congress.cache_hits
        result.bills = len(rows)
    if federal_register is not None:
        docs = document_rows(
            federal_register,
            searches if searches is not None else default_searches(since),
            index,
            result,
        )
        result.documents = len(docs)
        result.fr_queries = federal_register.queries
        result.fr_cache_hits = federal_register.cache_hits
        rows += docs
    found = set(result.existing)
    known = set(index.bills.values()) if congress is not None else set()
    if federal_register is not None:
        known |= set(index.fr_documents.values()) | set(index.executive_orders.values())
    # Known bills outside the searched Congresses are not expected to appear.
    known -= {rid for key, rid in index.bills.items() if key[0] not in congresses}
    result.missing_existing = sorted(known - found)
    result.existing = sorted(found)
    tmp = result.path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(result.path)
    summary_path = result.path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(result.summary(), indent=1) + "\n", encoding="utf-8")
    return result
