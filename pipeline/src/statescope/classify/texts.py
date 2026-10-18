"""Fetch, decode, and store bill text for the full classification stage.

Each document is requested once with getBillText (a static call: a doc_id's
text never changes). The JSON response is cached under
`.cache/legiscan/responses/getBillText/{doc_id}.json` and never requested
again; the extracted plain text is stored as `.cache/texts/{doc_id}.txt` with
`{doc_id}.meta.json` beside it. `.cache/texts/by_key.json` maps each bill key
to the document chosen for it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..ingest.legiscan import LegiScanClient, LegiScanError
from .common import ClassifyPaths, latest_text, read_json, real_date, write_json
from .textutil import PdfUnavailable, extract

OK = "ok"
UNAVAILABLE = "text_unavailable"


def response_path(legiscan_cache: Path, doc_id: int) -> Path:
    return legiscan_cache / "responses" / "getBillText" / f"{doc_id}.json"


def get_bill_text(client: LegiScanClient, doc_id: int) -> dict[str, Any]:
    """The getBillText `text` object, from cache when it has ever been fetched."""
    path = response_path(client.cache_dir, doc_id)
    if path.exists():
        return read_json(path)["response"]["text"]
    data = client.call("getBillText", id=doc_id)
    write_json(path, {"fetched_at": datetime.now(UTC).isoformat(), "response": data})
    return data["text"]


class TextStore:
    def __init__(self, directory: Path):
        self.dir = directory
        self._by_key: dict[str, int] | None = None

    @property
    def index_path(self) -> Path:
        return self.dir / "by_key.json"

    def by_key(self) -> dict[str, int]:
        if self._by_key is None:
            self._by_key = (
                {k: int(v) for k, v in read_json(self.index_path).items()}
                if self.index_path.exists()
                else {}
            )
        return self._by_key

    def save_index(self) -> None:
        write_json(self.index_path, dict(sorted(self.by_key().items())))

    def meta_path(self, doc_id: int) -> Path:
        return self.dir / f"{doc_id}.meta.json"

    def text_path(self, doc_id: int) -> Path:
        return self.dir / f"{doc_id}.txt"

    def meta(self, doc_id: int) -> dict[str, Any] | None:
        path = self.meta_path(doc_id)
        return read_json(path) if path.exists() else None

    def for_key(self, key: str) -> tuple[dict[str, Any] | None, str | None]:
        """(metadata, plain text or None) of the document stored for a bill."""
        doc_id = self.by_key().get(key)
        if doc_id is None:
            return None, None
        meta = self.meta(doc_id)
        if not meta:
            return None, None
        path = self.text_path(doc_id)
        text = (
            path.read_text(encoding="utf-8") if meta.get("status") == OK and path.exists() else None
        )
        return meta, text

    def store(self, key: str, meta: Mapping[str, Any], text: str | None) -> None:
        doc_id = int(meta["doc_id"])
        self.dir.mkdir(parents=True, exist_ok=True)
        if text is not None:
            self.text_path(doc_id).write_text(text, encoding="utf-8")
        write_json(self.meta_path(doc_id), dict(meta))
        self.by_key()[key] = doc_id


def decode_document(text_obj: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """(plain text, None) or (None, reason it is unavailable)."""
    try:
        raw = base64.b64decode(text_obj.get("doc") or "", validate=True)
    except (binascii.Error, ValueError):
        return None, "document is not valid base64"
    if not raw:
        return None, "empty document"
    try:
        text = extract(raw, text_obj.get("mime"))
    except PdfUnavailable as exc:
        return None, str(exc)
    except ValueError as exc:
        return None, str(exc)
    if len(text.strip()) < 100:
        return None, "no usable text extracted"
    return text, None


@dataclass
class FetchResult:
    fetched: list[str] = field(default_factory=list)  # keys with a new API query
    cached: list[str] = field(default_factory=list)
    unavailable: list[tuple[str, str]] = field(default_factory=list)  # (key, reason)
    no_text: list[str] = field(default_factory=list)  # bill lists no text at all
    skipped_budget: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    queries: int = 0
    month_total: int = 0


def fetch_texts(
    cp: ClassifyPaths,
    client: LegiScanClient,
    bills: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    max_queries: int,
    refresh_extract: bool = False,
    progress: Callable[[str], None] | None = None,
) -> FetchResult:
    """Fetch and store the latest text for each (key, LegiScan bill) pair."""
    store = TextStore(cp.texts)
    result = FetchResult()
    before = client.usage.used()
    for key, bill in bills:
        text_info = latest_text(bill.get("texts") or [])
        if text_info is None:
            result.no_text.append(key)
            store.store(
                key,
                {
                    "doc_id": 0 - int(bill.get("bill_id") or 0),
                    "bill_id": bill.get("bill_id"),
                    "key": key,
                    "status": UNAVAILABLE,
                    "reason": "LegiScan lists no text for this bill",
                },
                None,
            )
            continue
        doc_id = int(text_info["doc_id"])
        meta = store.meta(doc_id)
        if meta and not refresh_extract and (meta.get("status") == OK or meta.get("decoded")):
            store.by_key()[key] = doc_id
            result.cached.append(key)
            if meta.get("status") != OK:
                result.unavailable.append((key, str(meta.get("reason"))))
            continue
        cached_response = response_path(cp.legiscan, doc_id).exists()
        if not cached_response and client.usage.used() - before >= max_queries:
            result.skipped_budget.append(key)
            continue
        try:
            obj = get_bill_text(client, doc_id)
        except LegiScanError as exc:
            result.errors.append((key, str(exc)))
            if "limit" in str(exc).lower():
                break
            continue
        (result.cached if cached_response else result.fetched).append(key)
        if progress and not cached_response:
            progress(f"getBillText {doc_id} ({key})")
        text, reason = decode_document(obj)
        meta = {
            "doc_id": doc_id,
            "bill_id": bill.get("bill_id"),
            "key": key,
            "type": text_info.get("type") or obj.get("type"),
            "date": real_date(text_info.get("date")) or real_date(obj.get("date")),
            "mime": obj.get("mime") or text_info.get("mime"),
            "state_link": text_info.get("state_link") or obj.get("state_link") or None,
            "status": OK if text else UNAVAILABLE,
            "decoded": True,
            "chars": len(text) if text else 0,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
            "extracted_at": datetime.now(UTC).date().isoformat(),
        }
        if reason:
            meta["reason"] = reason
            result.unavailable.append((key, reason))
        store.store(key, meta, text)
    store.save_index()
    result.month_total = client.usage.used()
    result.queries = result.month_total - before
    return result
