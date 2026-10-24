"""Federal Register API client and document normalization.

Uses the documented public API (https://www.federalregister.gov/developers/
documentation/api/v1), which needs no key. Responses are cached under
`.cache/federal_register/` for `MAX_AGE`.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..ingest.http import HTTPError, RateLimiter, Transport, urllib_transport
from .cache import JsonCache, cache_key

API_URL = "https://www.federalregister.gov/api/v1/documents.json"
MIN_INTERVAL = 1.0
MAX_AGE = timedelta(hours=20)
PER_PAGE = 100
DEFAULT_TERM = '"artificial intelligence"'

FIELDS = (
    "document_number",
    "type",
    "subtype",
    "title",
    "publication_date",
    "signing_date",
    "effective_on",
    "executive_order_number",
    "agencies",
    "docket_ids",
    "citation",
    "html_url",
    "pdf_url",
    "abstract",
)

# Federal Register document types.
AGENCY_TYPES = {"RULE": "Rule", "PRORULE": "Proposed Rule", "NOTICE": "Notice"}


class FederalRegisterError(Exception):
    """A request failed or the API answered with something unreadable."""


@dataclass(frozen=True)
class Search:
    """One documents.json search; `name` labels its results in the output."""

    name: str
    conditions: tuple[tuple[str, str], ...]


def executive_orders(since: date, term: str = DEFAULT_TERM) -> Search:
    return Search(
        "executive-orders",
        (
            ("conditions[term]", term),
            ("conditions[type][]", "PRESDOCU"),
            ("conditions[presidential_document_type][]", "executive_order"),
            ("conditions[publication_date][gte]", since.isoformat()),
        ),
    )


def presidential_memoranda(since: date, term: str = DEFAULT_TERM) -> Search:
    return Search(
        "presidential-memoranda",
        (
            ("conditions[term]", term),
            ("conditions[type][]", "PRESDOCU"),
            ("conditions[presidential_document_type][]", "memorandum"),
            ("conditions[publication_date][gte]", since.isoformat()),
        ),
    )


def agency_documents(
    agency: str,
    since: date,
    types: Sequence[str] = tuple(AGENCY_TYPES),
    term: str = DEFAULT_TERM,
) -> Search:
    return Search(
        f"{agency}:{','.join(t.lower() for t in types)}",
        (
            ("conditions[term]", term),
            ("conditions[agencies][]", agency),
            *(("conditions[type][]", t) for t in types),
            ("conditions[publication_date][gte]", since.isoformat()),
        ),
    )


class FederalRegisterClient:
    def __init__(
        self,
        cache_dir: Path,
        *,
        transport: Transport | None = None,
        limiter: RateLimiter | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.cache = JsonCache(cache_dir, now=now or (lambda: datetime.now(UTC)))
        self.transport = transport or urllib_transport()
        self.limiter = limiter or RateLimiter(MIN_INTERVAL)
        self.queries = 0
        self.cache_hits = 0

    def _page(self, params: list[tuple[str, str]]) -> dict[str, Any]:
        file = self.cache.file("responses", cache_key("documents", dict(_flatten(params))))
        cached = self.cache.get(file, MAX_AGE)
        if cached is not None:
            self.cache_hits += 1
            return cached
        self.limiter.wait()
        self.queries += 1
        # The API repeats keys (fields[], conditions[type][]), so the query string is
        # built here and passed whole rather than as a mapping.
        url = f"{API_URL}?{urllib.parse.urlencode(params)}"
        try:
            body = self.transport(url, {}, {})
        except HTTPError as exc:
            raise FederalRegisterError(str(exc)) from None
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise FederalRegisterError("unreadable response from the Federal Register") from None
        if not isinstance(data, dict) or "errors" in data:
            raise FederalRegisterError(f"Federal Register error: {str(data)[:200]}")
        self.cache.put(file, data, params=params)
        return data

    def search(self, search: Search) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            params = [
                *search.conditions,
                ("order", "oldest"),
                ("per_page", str(PER_PAGE)),
                ("page", str(page)),
                *(("fields[]", f) for f in FIELDS),
            ]
            data = self._page(params)
            yield from data.get("results") or []
            if page >= int(data.get("total_pages") or 1) or not data.get("results"):
                return
            page += 1


def _flatten(params: list[tuple[str, str]]) -> Mapping[str, str]:
    """Repeated keys made unique, for the cache key only."""
    out: dict[str, str] = {}
    for i, (k, v) in enumerate(params):
        out[f"{i:03d}:{k}"] = v
    return out


def normalize_document(doc: Mapping[str, Any], search: str) -> dict[str, Any]:
    agencies = doc.get("agencies") or []
    names = [a.get("name") or a.get("raw_name") for a in agencies]
    eo = doc.get("executive_order_number")
    return {
        "source": "federal_register",
        "jurisdiction": "us",
        "document_number": doc.get("document_number"),
        "type": doc.get("type"),
        "subtype": doc.get("subtype"),
        "title": doc.get("title"),
        "publication_date": doc.get("publication_date"),
        "signing_date": doc.get("signing_date"),
        "effective_on": doc.get("effective_on"),
        "executive_order_number": str(eo) if eo else None,
        "agency": names[0] if names else None,
        "agencies": [
            {"name": a.get("name") or a.get("raw_name"), "slug": a.get("slug")} for a in agencies
        ],
        "docket_ids": doc.get("docket_ids") or [],
        "citation": doc.get("citation"),
        "html_url": doc.get("html_url"),
        "pdf_url": doc.get("pdf_url"),
        "abstract": doc.get("abstract"),
        "search": search,
        "external_ids": {"federal_register_document_number": doc.get("document_number")},
    }
