"""Congress.gov API v3 client and bill normalization.

Uses the documented API only (https://api.congress.gov/). The API has no
full-text search, so bills are discovered by scanning two listings for each
Congress and matching keywords locally:

- `/bill/{congress}`: every bill with its display title;
- `/summaries/{congress}`: every CRS summary, matched on its text;
- optionally `/bill/{congress}/{type}/{number}/titles` for every listed bill,
  so a bill whose display title and summary miss a keyword can still match on
  its official or short titles. That is one request per bill, so it is off by
  default; each response is cached until the bill's update date changes, which
  makes an interrupted run resume where it stopped.

Details (bill, actions, cosponsors, text versions, summaries, titles) are then
fetched for each match. Every response is cached under `.cache/congress/`:
listing pages for `LIST_MAX_AGE`, and a bill's details until its
`updateDateIncludingText` changes. The API key travels in the query string,
so it is never written to the cache, logs, or error messages.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..ingest.http import HTTPError, RateLimiter, Transport, urllib_transport
from .actions import normalize_action
from .cache import JsonCache, cache_key

API_URL = "https://api.congress.gov/v3"
# The documented default is 5,000 requests an hour; stay under it.
MIN_INTERVAL = 0.75
PAGE_SIZE = 250
LIST_MAX_AGE = timedelta(hours=20)
RETRY_STATUSES = ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504")

# Congress.gov bill type -> (citation prefix, congress.gov URL segment, classification)
BILL_TYPES: dict[str, tuple[str, str, str]] = {
    "hr": ("H.R.", "house-bill", "bill"),
    "s": ("S.", "senate-bill", "bill"),
    "hres": ("H.Res.", "house-resolution", "resolution"),
    "sres": ("S.Res.", "senate-resolution", "resolution"),
    "hjres": ("H.J.Res.", "house-joint-resolution", "joint-resolution"),
    "sjres": ("S.J.Res.", "senate-joint-resolution", "joint-resolution"),
    "hconres": ("H.Con.Res.", "house-concurrent-resolution", "concurrent-resolution"),
    "sconres": ("S.Con.Res.", "senate-concurrent-resolution", "concurrent-resolution"),
}


class CongressError(Exception):
    """A request failed or the API answered with something unreadable."""


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def congress_gov_url(congress: int, bill_type: str, number: str | int) -> str:
    segment = BILL_TYPES[bill_type.lower()][1]
    return f"https://www.congress.gov/bill/{ordinal(congress)}-congress/{segment}/{number}"


def strip_html(text: str | None) -> str | None:
    if not text:
        return text
    text = re.sub(r"<\s*(?:br|/p|/li)\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def bill_key(congress: int | str, bill_type: str, number: int | str) -> tuple[int, str, str]:
    return int(congress), bill_type.lower(), str(number)


class CongressClient:
    def __init__(
        self,
        api_key: str,
        cache_dir: Path,
        *,
        transport: Transport | None = None,
        limiter: RateLimiter | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        retries: int = 3,
    ):
        if not api_key:
            raise CongressError("CONGRESS_API_KEY is not set")
        self._key = api_key
        self.cache = JsonCache(cache_dir, now=now)
        self.transport = transport or urllib_transport()
        self.limiter = limiter or RateLimiter(MIN_INTERVAL)
        self.now = now or (lambda: datetime.now(UTC))
        self._sleep = sleep or self.limiter.sleep
        self.retries = retries
        self.queries = 0
        self.cache_hits = 0

    def __repr__(self) -> str:  # keep the key out of tracebacks and logs
        return f"CongressClient(cache_dir={str(self.cache.root)!r})"

    # -- transport ---------------------------------------------------------

    def _fetch(self, path: str, params: Mapping[str, Any]) -> dict[str, Any]:
        query = {**{k: str(v) for k, v in params.items()}, "format": "json"}
        attempt = 0
        while True:
            self.limiter.wait()
            self.queries += 1
            try:
                body = self.transport(f"{API_URL}{path}", {**query, "api_key": self._key}, {})
                break
            except HTTPError as exc:
                message = str(exc).replace(self._key, "***")
                if attempt < self.retries and message.startswith(RETRY_STATUSES):
                    attempt += 1
                    self._sleep(30.0 * attempt)
                    continue
                raise CongressError(message) from None
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CongressError(f"unreadable response from {API_URL}{path}") from None
        if not isinstance(data, dict):
            raise CongressError(f"unexpected response from {API_URL}{path}")
        if "error" in data:
            raise CongressError(f"{path}: {str(data['error'])[:200]}".replace(self._key, "***"))
        return data

    def get(
        self, path: str, params: Mapping[str, Any] | None = None, *, max_age: timedelta | None
    ) -> dict[str, Any]:
        """One response, from the cache when it is younger than `max_age`."""
        params = dict(params or {})
        file = self.cache.file("responses", cache_key(path, params))
        cached = self.cache.get(file, max_age)
        if cached is not None:
            self.cache_hits += 1
            return cached
        data = self._fetch(path, params)
        self.cache.put(file, data, request=path, params=params)
        return data

    def pages(
        self,
        path: str,
        key: str,
        params: Mapping[str, Any] | None = None,
        *,
        max_age: timedelta | None = LIST_MAX_AGE,
        page_size: int = PAGE_SIZE,
    ) -> Iterator[dict[str, Any]]:
        """Every item of a paginated listing."""
        offset = 0
        while True:
            data = self.get(
                path, {**(params or {}), "limit": page_size, "offset": offset}, max_age=max_age
            )
            items = data.get(key) or []
            yield from items
            count = int((data.get("pagination") or {}).get("count") or 0)
            offset += page_size
            if not items or offset >= count:
                return

    # -- listings ----------------------------------------------------------

    def bills(self, congress: int) -> Iterator[dict[str, Any]]:
        return self.pages(f"/bill/{congress}", "bills", {"sort": "updateDate asc"})

    def summaries(self, congress: int, since: datetime) -> Iterator[dict[str, Any]]:
        # Without a date window the endpoint returns nothing.
        params = {
            "fromDateTime": since.strftime("%Y-%m-%dT00:00:00Z"),
            "toDateTime": self.now().strftime("%Y-%m-%dT00:00:00Z"),
            "sort": "updateDate asc",
        }
        return self.pages(f"/summaries/{congress}", "summaries", params)

    # -- details -----------------------------------------------------------

    def bill_details(
        self, congress: int, bill_type: str, number: str, update_date: str | None = None
    ) -> dict[str, Any]:
        """Bill, actions, cosponsors, text versions, summaries, and titles.

        Cached per bill; refetched only when `update_date` (the listing's
        `updateDateIncludingText`) differs from the cached copy's, or when no
        update date is known and the cache is older than `LIST_MAX_AGE`.
        """
        congress, bill_type, number = bill_key(congress, bill_type, number)
        file = self.cache.file("bills", str(congress), bill_type, f"{number}.json")
        envelope = self.cache.meta(file)
        if envelope and (
            (update_date and envelope.get("update_date") == update_date)
            or (not update_date and self.cache.get(file, LIST_MAX_AGE) is not None)
        ):
            self.cache_hits += 1
            return envelope["data"]
        base = f"/bill/{congress}/{bill_type}/{number}"
        bill = self._fetch(base, {}).get("bill") or {}
        data = {"bill": bill}
        for part, key in (
            ("actions", "actions"),
            ("cosponsors", "cosponsors"),
            ("text", "textVersions"),
            ("summaries", "summaries"),
            ("titles", "titles"),
        ):
            data[key] = list(self._all(f"{base}/{part}", key))
        self.cache.put(file, data, update_date=update_date or bill.get("updateDateIncludingText"))
        return data

    def titles_cached(
        self, congress: int, bill_type: str, number: str, update_date: str | None
    ) -> bool:
        """Whether `bill_titles` would answer from the cache, without a request."""
        congress, bill_type, number = bill_key(congress, bill_type, number)
        envelope = self.cache.meta(
            self.cache.file("titles", str(congress), bill_type, f"{number}.json")
        )
        return bool(envelope) and envelope.get("update_date") == update_date

    def bill_titles(
        self, congress: int, bill_type: str, number: str, update_date: str | None
    ) -> list[str]:
        """Every title of one bill, cached until `update_date` changes."""
        congress, bill_type, number = bill_key(congress, bill_type, number)
        file = self.cache.file("titles", str(congress), bill_type, f"{number}.json")
        envelope = self.cache.meta(file)
        if envelope and envelope.get("update_date") == update_date:
            self.cache_hits += 1
            return envelope["data"]
        items = self._all(f"/bill/{congress}/{bill_type}/{number}/titles", "titles")
        titles = sorted({t["title"] for t in items if t.get("title")})
        self.cache.put(file, titles, update_date=update_date)
        return titles

    def _all(self, path: str, key: str) -> Iterator[dict[str, Any]]:
        offset = 0
        while True:
            data = self._fetch(path, {"limit": PAGE_SIZE, "offset": offset})
            items = data.get(key) or []
            yield from items
            count = int((data.get("pagination") or {}).get("count") or 0)
            offset += PAGE_SIZE
            if not items or offset >= count:
                return


# -- normalization -----------------------------------------------------------


def _person(p: Mapping[str, Any], role: str) -> dict[str, Any]:
    row = {
        "name": p.get("fullName"),
        "bioguide_id": p.get("bioguideId"),
        "party": p.get("party"),
        "state": p.get("state"),
        "role": role,
    }
    if role == "cosponsor":
        row["sponsorship_date"] = p.get("sponsorshipDate")
        row["original"] = p.get("isOriginalCosponsor")
        if p.get("sponsorshipWithdrawnDate"):
            row["withdrawn_date"] = p.get("sponsorshipWithdrawnDate")
    return row


def _texts(versions: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for v in versions:
        formats = {f.get("type"): f.get("url") for f in v.get("formats") or []}
        rows.append(
            {
                "type": v.get("type"),
                "date": (v.get("date") or "")[:10] or None,
                "url": formats.get("Formatted Text") or next(iter(formats.values()), None),
                "pdf_url": formats.get("PDF"),
                "xml_url": formats.get("Formatted XML"),
            }
        )
    return sorted(rows, key=lambda t: t["date"] or "")


def latest_summary(summaries: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    ordered = sorted(
        summaries, key=lambda s: (s.get("actionDate") or "", s.get("updateDate") or "")
    )
    if not ordered:
        return None
    s = ordered[-1]
    return {
        "action_date": s.get("actionDate"),
        "action_desc": s.get("actionDesc"),
        "version_code": s.get("versionCode"),
        "text": strip_html(s.get("text")),
    }


def normalize_bill(details: Mapping[str, Any]) -> dict[str, Any]:
    """A candidate row in the shape the other ingest code uses."""
    bill = details.get("bill") or {}
    congress = int(bill["congress"])
    bill_type = str(bill["type"]).lower()
    number = str(bill["number"])
    prefix, _segment, classification = BILL_TYPES.get(bill_type, (bill_type.upper(), "", "bill"))
    actions = sorted(
        (normalize_action(a) for a in details.get("actions") or []),
        key=lambda a: a["date"] or "",
    )
    latest = bill.get("latestAction") or {}
    titles = details.get("titles") or []
    official = next(
        (t.get("title") for t in titles if (t.get("titleType") or "").startswith("Official Title")),
        None,
    )
    sponsors = [_person(p, "sponsor") for p in bill.get("sponsors") or []]
    sponsors += [_person(p, "cosponsor") for p in details.get("cosponsors") or []]
    summary = latest_summary(details.get("summaries") or [])
    return {
        "source": "congress",
        "jurisdiction": "us",
        "congress": congress,
        "session": str(congress),
        "type": bill_type,
        "number": number,
        "identifier": f"{prefix} {number}",
        "bill_slug": f"{bill_type}-{number}",
        "record_id": f"us-{congress}-{bill_type}-{number}",
        "classification": classification,
        "title": bill.get("title"),
        "official_title": official,
        "titles": sorted({t.get("title") for t in titles if t.get("title")}),
        "introduced_date": bill.get("introducedDate"),
        "origin_chamber": bill.get("originChamber"),
        "policy_area": (bill.get("policyArea") or {}).get("name"),
        "latest_action": {"date": latest.get("actionDate"), "text": latest.get("text")},
        "actions": actions,
        "sponsors": sponsors,
        "texts": _texts(details.get("textVersions") or []),
        "url": bill.get("legislationUrl") or congress_gov_url(congress, bill_type, number),
        "summary": summary["text"] if summary else None,
        "summary_version": summary["action_desc"] if summary else None,
        "update_date": bill.get("updateDateIncludingText") or bill.get("updateDate"),
        "external_ids": {"congress_gov": f"{congress}/{bill_type}/{number}"},
    }
