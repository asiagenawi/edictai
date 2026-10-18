"""Full-text LegiScan searches that catch AI bills whose titles do not say so.

Searches go through `LegiScanClient.search_raw`, which caches each page, so
an identical search is never repeated within the hour. Bills a search finds
that are not in any cached dataset are fetched once with getBill.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .legiscan import LegiScanClient, _read_json, _write_json

SEARCHES: tuple[str, ...] = (
    '"artificial intelligence" AND (school OR student OR education OR university OR college OR teacher)',
    '"generative artificial intelligence"',
    "deepfake AND (school OR student)",
    "chatbot AND (minor OR student OR school)",
    '"machine learning" AND (education OR student)',
)
# state=ALL already includes Congress: every US hit also appears in the ALL results.
SEARCH_STATES: tuple[str, ...] = ("ALL",)


def dataset_bill_index(cache_dir: Path) -> dict[int, tuple[int, str]]:
    """bill_id -> (session_id, zip member) across every cached dataset.

    Each dataset's part is cached beside it and rebuilt when its hash changes.
    """
    index: dict[int, tuple[int, str]] = {}
    for meta_path in sorted((cache_dir / "datasets").glob("*.json")):
        sid = int(meta_path.stem)
        archive = meta_path.with_suffix(".zip")
        if not archive.exists():
            continue
        dataset_hash = _read_json(meta_path).get("dataset_hash")
        part_path = cache_dir / "bill_index" / f"{sid}.json"
        part = _read_json(part_path) if part_path.exists() else {}
        if part.get("dataset_hash") != dataset_hash:
            members: dict[str, str] = {}
            with zipfile.ZipFile(archive) as zf:
                for name in zf.namelist():
                    if "/bill/" in name and name.endswith(".json"):
                        bill = json.loads(zf.read(name)).get("bill") or {}
                        if "bill_id" in bill:
                            members[str(bill["bill_id"])] = name
            part = {"dataset_hash": dataset_hash, "bills": members}
            _write_json(part_path, part)
        for bid, name in part["bills"].items():
            index[int(bid)] = (sid, name)
    return index


@dataclass
class SearchResult:
    hits: dict[int, dict[str, Any]] = field(default_factory=dict)
    searches: int = 0  # search pages read (cached or not)
    fetched_bills: list[int] = field(default_factory=list)
    queries: int = 0  # API queries sent this run
    month_total: int = 0


def run_searches(
    client: LegiScanClient,
    years: Iterable[int],
    *,
    searches: Iterable[str] = SEARCHES,
    states: Iterable[str] = SEARCH_STATES,
    progress: Callable[[str], None] | None = None,
) -> SearchResult:
    """Run every search for every state scope and year; fetch bills missing from datasets."""
    searches, states, years = tuple(searches), tuple(states), tuple(years)
    before = client.usage.used()
    result = SearchResult()
    for query in searches:
        for state in states:
            for year in years:
                page, total = 1, 1
                while page <= total:
                    found = client.search_raw(query, state=state, year=year, page=page)
                    result.searches += 1
                    total = int(found.get("summary", {}).get("page_total") or 1)
                    for row in found.get("results") or []:
                        hit = result.hits.setdefault(
                            int(row["bill_id"]),
                            {"change_hash": row.get("change_hash"), "queries": []},
                        )
                        if query not in hit["queries"]:
                            hit["queries"].append(query)
                    page += 1
                if progress:
                    progress(f"search {state} {year}: {query}")
    in_datasets = dataset_bill_index(client.cache_dir)
    for bill_id, hit in sorted(result.hits.items()):
        if bill_id in in_datasets:
            hit["source"] = "dataset"
            continue
        client.get_bill(bill_id, hit.get("change_hash"))
        hit["source"] = "getBill"
        result.fetched_bills.append(bill_id)
    _write_json(
        client.cache_dir / "fulltext.json",
        {
            "searched_at": datetime.now(UTC).isoformat(),
            "searches": list(searches),
            "hits": {str(k): v for k, v in sorted(result.hits.items())},
        },
    )
    result.month_total = client.usage.used()
    result.queries = result.month_total - before
    return result
