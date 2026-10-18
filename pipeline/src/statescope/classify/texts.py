"""Fetch, decode, and store bill text for the full classification stage.

Each document is requested once with getBillText (a static call: a doc_id's
text never changes). The JSON response is cached under
`.cache/legiscan/responses/getBillText/{doc_id}.json` and never requested
again; the extracted plain text is stored as `.cache/texts/{doc_id}.txt` with
`{doc_id}.meta.json` beside it. `.cache/texts/by_key.json` maps each bill key
to the document chosen for it (the latest version), and
`.cache/texts/earlier_by_key.json` to an earlier version fetched beside it, for
bills whose AI language may have been amended out.
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

from ..ingest.candidates import KEYWORDS, WEAK_KEYWORDS
from ..ingest.legiscan import LegiScanClient, LegiScanError
from .common import ClassifyPaths, latest_text, read_json, real_date, write_json
from .textutil import PdfUnavailable, extract

OK = "ok"
UNAVAILABLE = "text_unavailable"
VERSIONS = ("latest", "earliest", "ai")
_AI_TERMS = [p for name, p in KEYWORDS.items() if name not in WEAK_KEYWORDS]


def mentions_ai(text: str | None) -> bool:
    """Whether the text uses any AI keyword other than the weak ones ("algorithm")."""
    return bool(text) and any(p.search(text) for p in _AI_TERMS)


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
        self._earlier: dict[str, int] | None = None

    @property
    def index_path(self) -> Path:
        return self.dir / "by_key.json"

    @property
    def earlier_index_path(self) -> Path:
        return self.dir / "earlier_by_key.json"

    @staticmethod
    def _load_index(path: Path) -> dict[str, int]:
        return {k: int(v) for k, v in read_json(path).items()} if path.exists() else {}

    def by_key(self) -> dict[str, int]:
        if self._by_key is None:
            self._by_key = self._load_index(self.index_path)
        return self._by_key

    def earlier_by_key(self) -> dict[str, int]:
        if self._earlier is None:
            self._earlier = self._load_index(self.earlier_index_path)
        return self._earlier

    def save_index(self) -> None:
        write_json(self.index_path, dict(sorted(self.by_key().items())))
        if self._earlier is not None:
            write_json(self.earlier_index_path, dict(sorted(self._earlier.items())))

    def meta_path(self, doc_id: int) -> Path:
        return self.dir / f"{doc_id}.meta.json"

    def text_path(self, doc_id: int) -> Path:
        return self.dir / f"{doc_id}.txt"

    def meta(self, doc_id: int) -> dict[str, Any] | None:
        path = self.meta_path(doc_id)
        return read_json(path) if path.exists() else None

    def document(self, doc_id: int | None) -> tuple[dict[str, Any] | None, str | None]:
        """(metadata, plain text or None) of one stored document."""
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

    def for_key(self, key: str) -> tuple[dict[str, Any] | None, str | None]:
        """(metadata, plain text or None) of the latest document stored for a bill."""
        return self.document(self.by_key().get(key))

    def earlier_for_key(self, key: str) -> tuple[dict[str, Any] | None, str | None]:
        """(metadata, plain text or None) of the earlier version stored for a bill, if any."""
        return self.document(self.earlier_by_key().get(key))

    def texts_for_key(self, key: str) -> list[tuple[dict[str, Any], str]]:
        """Every stored version of a bill that has text: the latest first, then the earlier."""
        out = []
        for meta, text in (self.for_key(key), self.earlier_for_key(key)):
            if meta is not None and text is not None:
                out.append((meta, text))
        return out

    def write_document(self, meta: Mapping[str, Any], text: str | None) -> int:
        doc_id = int(meta["doc_id"])
        self.dir.mkdir(parents=True, exist_ok=True)
        if text is not None:
            self.text_path(doc_id).write_text(text, encoding="utf-8")
        write_json(self.meta_path(doc_id), dict(meta))
        return doc_id

    def store(self, key: str, meta: Mapping[str, Any], text: str | None) -> None:
        self.by_key()[key] = self.write_document(meta, text)

    def store_earlier(self, key: str, meta: Mapping[str, Any], text: str | None) -> None:
        self.earlier_by_key()[key] = self.write_document(meta, text)


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
    earlier: list[str] = field(default_factory=list)  # keys with an earlier version stored
    no_earlier: list[tuple[str, str]] = field(default_factory=list)  # (key, why not)
    queries: int = 0
    month_total: int = 0


@dataclass
class _Doc:
    meta: dict[str, Any]
    text: str | None
    decoded: bool  # decoded in this run (store it)
    queried: bool  # an API query was sent for it


class _OutOfBudget(Exception):
    pass


class _LimitReached(Exception):
    pass


def _document(
    cp: ClassifyPaths,
    client: LegiScanClient,
    store: TextStore,
    key: str,
    bill: Mapping[str, Any],
    text_info: Mapping[str, Any],
    *,
    budget_left: Callable[[], bool],
    refresh_extract: bool,
    result: FetchResult,
    progress: Callable[[str], None] | None,
) -> _Doc | None:
    """One version's metadata and text, from the store, the cached response, or the API.

    None when the request failed (recorded in result.errors). Raises
    _OutOfBudget when a query is needed and the run's budget is spent, and
    _LimitReached when the API reports the monthly limit.
    """
    doc_id = int(text_info["doc_id"])
    meta = store.meta(doc_id)
    if meta and not refresh_extract and (meta.get("status") == OK or meta.get("decoded")):
        _, text = store.document(doc_id)
        return _Doc(meta, text, decoded=False, queried=False)
    cached_response = response_path(cp.legiscan, doc_id).exists()
    if not cached_response and not budget_left():
        raise _OutOfBudget
    try:
        obj = get_bill_text(client, doc_id)
    except LegiScanError as exc:
        result.errors.append((key, str(exc)))
        if "limit" in str(exc).lower():
            raise _LimitReached from exc
        return None
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
    return _Doc(meta, text, decoded=True, queried=not cached_response)


def earlier_versions(
    texts: Iterable[Mapping[str, Any]], latest_doc_id: int | None, version: str
) -> list[dict[str, Any]]:
    """The versions to try, in order, as the earlier text for `version`.

    `earliest`: oldest first (the next oldest is tried when one has no usable
    text). `ai`: newest first, for the latest earlier version that mentions AI.
    The latest version itself is never listed.
    """
    rows = [dict(t) for t in texts if t.get("doc_id") and int(t["doc_id"]) != latest_doc_id]
    rows.sort(key=lambda t: (real_date(t.get("date")) or "", int(t["doc_id"])))
    return rows if version == "earliest" else rows[::-1]


def fetch_texts(
    cp: ClassifyPaths,
    client: LegiScanClient,
    bills: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    max_queries: int,
    refresh_extract: bool = False,
    version: str = "latest",
    progress: Callable[[str], None] | None = None,
) -> FetchResult:
    """Fetch and store the latest text for each (key, LegiScan bill) pair.

    With `version` "earliest" or "ai", also store one earlier version beside
    the latest (see `earlier_versions`); with "ai" it is kept only when its
    text mentions AI. The latest version is fetched first when it is not
    stored yet.
    """
    if version not in VERSIONS:
        raise ValueError(f"version must be one of {', '.join(VERSIONS)}")
    store = TextStore(cp.texts)
    result = FetchResult()
    before = client.usage.used()

    def budget_left() -> bool:
        return client.usage.used() - before < max_queries

    def doc(key, bill, info):
        return _document(
            cp,
            client,
            store,
            key,
            bill,
            info,
            budget_left=budget_left,
            refresh_extract=refresh_extract,
            result=result,
            progress=progress,
        )

    for key, bill in bills:
        texts = bill.get("texts") or []
        text_info = latest_text(texts)
        try:
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
            got = doc(key, bill, text_info)
            if got is None:
                continue
            (result.fetched if got.queried else result.cached).append(key)
            if got.decoded:
                store.store(key, got.meta, got.text)
            else:
                store.by_key()[key] = int(got.meta["doc_id"])
            if got.meta.get("status") != OK:
                result.unavailable.append((key, str(got.meta.get("reason"))))
            if version != "latest":
                _fetch_earlier(store, key, bill, int(text_info["doc_id"]), version, doc, result)
        except _OutOfBudget:
            result.skipped_budget.append(key)
        except _LimitReached:
            break
    store.save_index()
    result.month_total = client.usage.used()
    result.queries = result.month_total - before
    return result


def _fetch_earlier(
    store: TextStore,
    key: str,
    bill: Mapping[str, Any],
    latest_doc_id: int,
    version: str,
    doc: Callable[..., _Doc | None],
    result: FetchResult,
) -> None:
    candidates = earlier_versions(bill.get("texts") or [], latest_doc_id, version)
    if not candidates:
        result.no_earlier.append((key, "only one version"))
        return
    for info in candidates:
        got = doc(key, bill, info)
        if got is None:
            continue
        if got.decoded:
            store.write_document(got.meta, got.text)
        if got.text is None or (version == "ai" and not mentions_ai(got.text)):
            continue
        store.store_earlier(key, got.meta, got.text)
        result.earlier.append(key)
        return
    why = "no earlier version mentions AI" if version == "ai" else "no earlier version has text"
    result.no_earlier.append((key, why))
