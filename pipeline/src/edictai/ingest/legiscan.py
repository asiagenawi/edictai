"""LegiScan API client and bulk dataset download.

Bill data from LegiScan (https://legiscan.com) is licensed CC BY 4.0.

The client talks to the documented API only (https://api.legiscan.com/) and:

- spaces requests at least one second apart;
- checks the JSON `status` of every response;
- caches every response under `.cache/legiscan/`;
- counts queries per calendar month in `.cache/legiscan/usage.json` and
  refuses to send more than `MONTHLY_LIMIT` in a month;
- requests the dataset list at most once a week, and a dataset only when its
  `dataset_hash` differs from the cached copy.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .http import RateLimiter, Transport, urllib_transport
from .session_ids import assign_identifiers

API_URL = "https://api.legiscan.com/"
ATTRIBUTION = "LegiScan (https://legiscan.com), licensed CC BY 4.0"
MONTHLY_LIMIT = 8000
MIN_INTERVAL = 1.0
DATASET_LIST_MAX_AGE = timedelta(days=7)
# LegiScan allows an identical search once an hour; reuse cached results for a day.
SEARCH_MAX_AGE = timedelta(hours=24)
# getBill at most every three hours per bill.
BILL_MIN_AGE = timedelta(hours=3)

# LegiScan state_id -> postal abbreviation (LegiScan numbers states by name).
_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC US"
)
STATE_IDS: dict[int, str] = dict(enumerate(_STATES.split(), start=1))


class LegiScanError(Exception):
    """The API answered with status ERROR or an unreadable body."""


class QuotaExceeded(LegiScanError):
    """Sending another query would pass the monthly limit."""


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class Usage:
    """Monthly query counter persisted to a JSON file."""

    def __init__(
        self, path: Path, limit: int = MONTHLY_LIMIT, today: Callable[[], date] = date.today
    ):
        self.path = path
        self.limit = limit
        self.today = today

    def _load(self) -> dict[str, Any]:
        return _read_json(self.path) if self.path.exists() else {}

    def month(self) -> str:
        return self.today().strftime("%Y-%m")

    def used(self) -> int:
        return int(self._load().get(self.month(), {}).get("queries", 0))

    def reserve(self, op: str) -> int:
        """Count one query against this month, or raise QuotaExceeded."""
        data = self._load()
        entry = data.setdefault(self.month(), {"queries": 0, "by_op": {}})
        if entry["queries"] >= self.limit:
            raise QuotaExceeded(
                f"{entry['queries']} LegiScan queries already sent in {self.month()}; "
                f"the limit is {self.limit}"
            )
        entry["queries"] += 1
        entry["by_op"][op] = entry["by_op"].get(op, 0) + 1
        _write_json(self.path, data)
        return entry["queries"]


class LegiScanClient:
    def __init__(
        self,
        api_key: str,
        cache_dir: Path,
        *,
        transport: Transport | None = None,
        limiter: RateLimiter | None = None,
        usage: Usage | None = None,
    ):
        if not api_key:
            raise LegiScanError("LEGISCAN_API_KEY is not set")
        self._key = api_key
        self.cache_dir = cache_dir
        self.transport = transport or urllib_transport()
        self.limiter = limiter or RateLimiter(MIN_INTERVAL)
        self.usage = usage or Usage(cache_dir / "usage.json")

    def __repr__(self) -> str:  # keep the key out of tracebacks and logs
        return f"LegiScanClient(cache_dir={str(self.cache_dir)!r})"

    def call(self, op: str, **params: str | int) -> dict[str, Any]:
        """Send one query and return the parsed body; raises on status ERROR."""
        self.usage.reserve(op)
        self.limiter.wait()
        query = {"key": self._key, "op": op, **{k: str(v) for k, v in params.items()}}
        body = self.transport(API_URL, query, {})
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise LegiScanError(f"{op}: response is not JSON") from None
        if not isinstance(data, dict) or data.get("status") != "OK":
            alert = data.get("alert", {}) if isinstance(data, dict) else {}
            message = alert.get("message") if isinstance(alert, dict) else None
            raise LegiScanError(f"{op}: status {data.get('status')!r}: {message or 'no message'}")
        return data

    # -- cached operations ---------------------------------------------------

    @property
    def _list_path(self) -> Path:
        return self.cache_dir / "responses" / "getDatasetList.json"

    def dataset_list(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Every available dataset, from cache when fetched within the last week."""
        now = now or datetime.now(UTC)
        if self._list_path.exists():
            cached = _read_json(self._list_path)
            fetched = datetime.fromisoformat(cached["fetched_at"])
            if now - fetched < DATASET_LIST_MAX_AGE:
                return cached["response"]["datasetlist"]
        data = self.call("getDatasetList")
        _write_json(self._list_path, {"fetched_at": now.isoformat(), "response": data})
        return data["datasetlist"]

    def dataset_paths(self, session_id: int) -> tuple[Path, Path]:
        base = self.cache_dir / "datasets"
        return base / f"{session_id}.json", base / f"{session_id}.zip"

    def cached_hash(self, session_id: int) -> str | None:
        meta, archive = self.dataset_paths(session_id)
        if meta.exists() and archive.exists():
            return _read_json(meta).get("dataset_hash")
        return None

    def get_dataset(self, session_id: int, access_key: str) -> Path:
        """Download one dataset; the zip is decoded and stored beside its metadata."""
        data = self.call("getDataset", id=session_id, access_key=access_key)
        dataset = dict(data["dataset"])
        try:
            raw = base64.b64decode(dataset.pop("zip"), validate=True)
            zipfile.ZipFile(io.BytesIO(raw)).testzip()
        except (KeyError, binascii.Error, zipfile.BadZipFile) as exc:
            raise LegiScanError(f"getDataset {session_id}: unreadable archive ({exc})") from None
        meta, archive = self.dataset_paths(session_id)
        archive.parent.mkdir(parents=True, exist_ok=True)
        tmp = archive.with_suffix(".zip.tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, archive)
        _write_json(meta, {**{k: v for k, v in data.items() if k != "dataset"}, **dataset})
        return archive

    def search_raw(
        self,
        query: str,
        *,
        state: str = "ALL",
        year: int = 2,
        page: int = 1,
        now: datetime | None = None,
        max_age: timedelta = SEARCH_MAX_AGE,
    ) -> dict[str, Any]:
        """One page of getSearchRaw results, reused from cache while younger than max_age."""
        now = now or datetime.now(UTC)
        max_age = max(max_age, timedelta(hours=1))
        digest = hashlib.sha1(f"{state}|{year}|{page}|{query}".encode()).hexdigest()[:16]
        path = self.cache_dir / "responses" / "getSearchRaw" / f"{digest}.json"
        if path.exists():
            cached = _read_json(path)
            if now - datetime.fromisoformat(cached["fetched_at"]) < max_age:
                return cached["response"]["searchresult"]
        data = self.call("getSearchRaw", state=state, query=query, year=year, page=page)
        _write_json(
            path,
            {
                "fetched_at": now.isoformat(),
                "request": {"state": state, "query": query, "year": year, "page": page},
                "response": data,
            },
        )
        return data["searchresult"]

    def bill_path(self, bill_id: int) -> Path:
        return self.cache_dir / "responses" / "getBill" / f"{bill_id}.json"

    def get_bill(
        self, bill_id: int, change_hash: str | None = None, *, now: datetime | None = None
    ) -> dict[str, Any]:
        """The bill, from cache unless its change_hash differs and the copy is 3+ hours old."""
        now = now or datetime.now(UTC)
        path = self.bill_path(bill_id)
        if path.exists():
            cached = _read_json(path)
            bill = cached["response"]["bill"]
            fresh = now - datetime.fromisoformat(cached["fetched_at"]) < BILL_MIN_AGE
            if fresh or change_hash is None or bill.get("change_hash") == change_hash:
                return bill
        data = self.call("getBill", id=bill_id)
        _write_json(path, {"fetched_at": now.isoformat(), "response": data})
        return data["bill"]


# -- bulk download ------------------------------------------------------------


def state_abbr(entry: Mapping[str, Any]) -> str:
    abbr = entry.get("state_abbr") or entry.get("state")
    if isinstance(abbr, str) and abbr:
        return abbr.upper()
    return STATE_IDS[int(entry["state_id"])]


def overlaps(entry: Mapping[str, Any], since: date, until: date) -> bool:
    start = int(entry["year_start"])
    end = int(entry.get("year_end") or start)
    return end >= since.year and start <= until.year


@dataclass
class BulkResult:
    downloaded: list[dict[str, Any]] = field(default_factory=list)
    unchanged: list[dict[str, Any]] = field(default_factory=list)
    queries: int = 0  # sent during this run
    month_total: int = 0


def session_index(entries: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """session_id -> session facts plus our identifier, for every listed dataset."""
    rows = [{**e, "state": state_abbr(e)} for e in entries]
    identifiers = assign_identifiers(rows)
    keys = ("state", "year_start", "year_end", "special", "session_tag", "session_name")
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        sid = int(row["session_id"])
        index[str(sid)] = {
            "session_id": sid,
            **{k: row.get(k) for k in keys},
            "session_title": row.get("session_title"),
            "identifier": identifiers[sid],
        }
    return index


def bulk_download(
    client: LegiScanClient,
    since: date,
    until: date | None = None,
    *,
    states: Iterable[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> BulkResult:
    until = until or date.today()
    wanted = {s.upper() for s in states} if states else None
    before = client.usage.used()
    entries = client.dataset_list()
    index = session_index(entries)
    _write_json(client.cache_dir / "sessions.json", index)
    result = BulkResult()
    for entry in sorted(entries, key=lambda e: (state_abbr(e), int(e["session_id"]))):
        if not overlaps(entry, since, until):
            continue
        abbr = state_abbr(entry)
        if wanted and abbr not in wanted:
            continue
        sid = int(entry["session_id"])
        info = {**index[str(sid)], "dataset_hash": entry["dataset_hash"]}
        if client.cached_hash(sid) == entry["dataset_hash"]:
            result.unchanged.append(info)
            continue
        if progress:
            progress(f"getDataset {abbr} {info['identifier']} ({entry.get('session_name')})")
        client.get_dataset(sid, entry["access_key"])
        result.downloaded.append(info)
    result.month_total = client.usage.used()
    result.queries = result.month_total - before
    return result
