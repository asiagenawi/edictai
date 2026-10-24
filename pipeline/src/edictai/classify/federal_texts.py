"""Fetch and store the text of federal candidates.

Bills: the text versions come from the Congress.gov API
(`/bill/{congress}/{type}/{number}/text`, cached under `.cache/congress/`),
and each version's document is the Formatted Text file the API links to
(the PDF when there is none). Federal Register documents: the document's
metadata comes from the Federal Register API
(`/api/v1/documents/{number}.json`), and its text from the `raw_text_url`
that response gives.

Downloaded documents never change, so each is fetched once and its raw bytes
are kept under `.cache/congress/documents/` or `.cache/federal_register/text/`.
Extracted text goes to the federal text store (`.cache/texts/federal/`), in the
same layout as LegiScan texts, with string ids: the document's file stem
(`BILLS-119hr5351ih`, `PLAW-118publ50`) or `FR-{document number}`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..federal.congress import LIST_MAX_AGE, CongressClient, CongressError, _texts
from ..federal.federal_register import MIN_INTERVAL as FR_INTERVAL
from ..ingest.http import HTTPError, RateLimiter, Transport, urllib_transport
from .common import ClassifyPaths, read_json, write_json
from .federal import _document_type, order_versions, version_id
from .texts import OK, UNAVAILABLE, VERSIONS, FetchResult, TextStore, mentions_ai
from .textutil import PdfUnavailable, extract

FR_DOCUMENT_URL = "https://www.federalregister.gov/api/v1/documents/{number}.json"
# Congress.gov document files are not API calls, but are spaced the same way.
DOCUMENT_INTERVAL = 0.75


class _OutOfBudget(Exception):
    pass


class Fetcher:
    """Network access for one run: the Congress.gov client, plain downloads, and a budget."""

    def __init__(
        self,
        cp: ClassifyPaths,
        congress: CongressClient | None,
        *,
        max_queries: int,
        transport: Transport | None = None,
        document_limiter: RateLimiter | None = None,
        fr_limiter: RateLimiter | None = None,
    ):
        self.cp = cp
        self.congress = congress
        self.max_queries = max_queries
        self.transport = transport or urllib_transport()
        self.document_limiter = document_limiter or RateLimiter(DOCUMENT_INTERVAL)
        self.fr_limiter = fr_limiter or RateLimiter(FR_INTERVAL)
        self.downloads = 0
        self._congress_start = congress.queries if congress else 0

    @property
    def queries(self) -> int:
        api = (self.congress.queries - self._congress_start) if self.congress else 0
        return api + self.downloads

    def budget_left(self) -> bool:
        return self.queries < self.max_queries

    def _need(self) -> None:
        if not self.budget_left():
            raise _OutOfBudget

    # -- Congress.gov ---------------------------------------------------------

    def bill_versions(self, row: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The bill's text versions from the API (a cached listing is reused for a day)."""
        if self.congress is None:
            raise CongressError("CONGRESS_API_KEY is not set")
        path = f"/bill/{row['congress']}/{row['type']}/{row['number']}/text"
        file = self.congress.cache.file("responses", _listing_name(path))
        if self.congress.cache.get(file, LIST_MAX_AGE) is None:
            self._need()
        items = list(self.congress.pages(path, "textVersions", max_age=LIST_MAX_AGE))
        return _texts(items)

    def _get(self, url: str, limiter: RateLimiter) -> bytes:
        self._need()
        limiter.wait()
        self.downloads += 1
        return self.transport(url, {}, {})

    def download(self, url: str, path: Path, limiter: RateLimiter) -> tuple[bytes, bool]:
        """(raw bytes, whether a request was sent): from `path` when downloaded before."""
        if path.exists():
            return path.read_bytes(), False
        body = self._get(url, limiter)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        return body, True

    # -- Federal Register -------------------------------------------------------

    def fr_document(self, number: str) -> tuple[dict[str, Any], bool]:
        file = self.cp.federal_register / "documents" / f"{number}.json"
        if file.exists():
            return read_json(file)["data"], False
        url = FR_DOCUMENT_URL.format(number=number)
        body = self._get(url, self.fr_limiter)
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPError(f"unreadable response for FR document {number}") from None
        if not isinstance(data, dict) or "errors" in data:
            raise HTTPError(f"Federal Register error for {number}: {str(data)[:200]}")
        write_json(file, {"fetched": datetime.now(UTC).isoformat(), "url": url, "data": data})
        return data, True


def _listing_name(path: str) -> str:
    from ..federal.cache import cache_key
    from ..federal.congress import PAGE_SIZE

    return cache_key(path, {"limit": PAGE_SIZE, "offset": 0})


def _decode(raw: bytes, mime: str) -> tuple[str | None, str | None]:
    try:
        text = extract(raw, mime)
    except (PdfUnavailable, ValueError) as exc:
        return None, str(exc)
    if len(text.strip()) < 100:
        return None, "no usable text extracted"
    return text, None


def _meta(key: str, doc_id: str, text: str | None, reason: str | None, **fields: Any) -> dict:
    meta = {
        "doc_id": doc_id,
        "key": key,
        **fields,
        "status": OK if text else UNAVAILABLE,
        "decoded": True,
        "chars": len(text) if text else 0,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
        "extracted_at": datetime.now(UTC).date().isoformat(),
    }
    if reason:
        meta["reason"] = reason
    return meta


def _bill_document(
    fetcher: Fetcher,
    store: TextStore,
    key: str,
    version: Mapping[str, Any],
    refresh_extract: bool,
) -> tuple[dict[str, Any], str | None, bool]:
    """(metadata, text, whether a request was sent) for one bill text version."""
    doc_id = version_id(version)
    if doc_id is None:
        raise ValueError(f"no document URL for version {version.get('type')}")
    meta = store.meta(doc_id)
    if meta and not refresh_extract and meta.get("decoded"):
        _, text = store.document(doc_id)
        return meta, text, False
    url = version.get("url") or version.get("pdf_url")
    mime = "text/html" if url == version.get("url") else "application/pdf"
    suffix = ".htm" if mime == "text/html" else ".pdf"
    raw, queried = fetcher.download(
        url, fetcher.cp.congress / "documents" / f"{doc_id}{suffix}", fetcher.document_limiter
    )
    text, reason = _decode(raw, mime)
    meta = _meta(
        key,
        doc_id,
        text,
        reason,
        type=version.get("type"),
        date=version.get("date"),
        url=url,
        mime=mime,
        source="congress.gov",
    )
    store.write_document(meta, text)
    return meta, text, queried


def _fr_text(
    fetcher: Fetcher, store: TextStore, key: str, row: Mapping[str, Any], refresh_extract: bool
) -> tuple[dict[str, Any], str | None, bool]:
    number = str(row["document_number"])
    doc_id = f"FR-{number}"
    meta = store.meta(doc_id)
    if meta and not refresh_extract and meta.get("decoded"):
        _, text = store.document(doc_id)
        return meta, text, False
    doc, queried = fetcher.fr_document(number)
    url = doc.get("raw_text_url")
    fields = {
        "type": _document_type(row),
        "date": doc.get("publication_date") or row.get("publication_date"),
        "url": doc.get("html_url") or row.get("html_url"),
        "text_url": url,
        "citation": doc.get("citation"),
        "mime": "text/html",
        "source": "federalregister.gov",
    }
    if not url:
        meta = _meta(key, doc_id, None, "the Federal Register lists no text", **fields)
        store.write_document(meta, None)
        return meta, None, queried
    raw, sent = fetcher.download(
        url, fetcher.cp.federal_register / "text" / f"{number}.txt", fetcher.fr_limiter
    )
    text, reason = _decode(raw, "text/html")
    meta = _meta(key, doc_id, text, reason, **fields)
    store.write_document(meta, text)
    return meta, text, queried or sent


def fetch_federal_texts(
    cp: ClassifyPaths,
    fetcher: Fetcher,
    rows: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    refresh_extract: bool = False,
    version: str = "latest",
    progress: Callable[[str], None] | None = None,
) -> FetchResult:
    """Fetch and store the latest text of each (key, federal candidate row).

    With `version` "earliest" or "ai", a bill also gets one earlier version
    stored beside the latest, chosen as for LegiScan bills (see
    `texts.earlier_versions`). Federal Register documents have one version.
    """
    if version not in VERSIONS:
        raise ValueError(f"version must be one of {', '.join(VERSIONS)}")
    store = TextStore(cp.texts)
    result = FetchResult()
    say = progress or (lambda _m: None)
    for key, row in rows:
        try:
            if row.get("source") == "federal_register":
                meta, _text, queried = _fr_text(fetcher, store, key, row, refresh_extract)
                if queried:
                    say(f"federal register {row['document_number']} ({key})")
            else:
                versions = order_versions(fetcher.bill_versions(row))
                versions = [v for v in versions if version_id(v)]
                if not versions:
                    result.no_text.append(key)
                    store.store(
                        key,
                        {
                            "doc_id": f"none-{key}",
                            "key": key,
                            "status": UNAVAILABLE,
                            "reason": "Congress.gov lists no text for this bill",
                        },
                        None,
                    )
                    continue
                meta, _text, queried = _bill_document(
                    fetcher, store, key, versions[-1], refresh_extract
                )
                if queried:
                    say(f"congress.gov {meta['doc_id']} ({key})")
            store.by_key()[key] = meta["doc_id"]
            (result.fetched if queried else result.cached).append(key)
            if meta.get("status") != OK:
                result.unavailable.append((key, str(meta.get("reason"))))
            if version != "latest" and row.get("source") == "congress":
                _earlier(fetcher, store, key, versions, version, refresh_extract, result)
        except _OutOfBudget:
            result.skipped_budget.append(key)
        except (CongressError, HTTPError, ValueError) as exc:
            result.errors.append((key, str(exc)))
    store.save_index()
    result.queries = fetcher.queries
    result.month_total = fetcher.queries
    return result


def _earlier(
    fetcher: Fetcher,
    store: TextStore,
    key: str,
    versions: list[dict[str, Any]],
    version: str,
    refresh_extract: bool,
    result: FetchResult,
) -> None:
    earlier = versions[:-1] if version == "earliest" else versions[:-1][::-1]
    if not earlier:
        result.no_earlier.append((key, "only one version"))
        return
    for info in earlier:
        meta, text, _ = _bill_document(fetcher, store, key, info, refresh_extract)
        if text is None or (version == "ai" and not mentions_ai(text)):
            continue
        store.earlier_by_key()[key] = meta["doc_id"]
        result.earlier.append(key)
        return
    why = "no earlier version mentions AI" if version == "ai" else "no earlier version has text"
    result.no_earlier.append((key, why))
