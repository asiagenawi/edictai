"""Match a watched URL to document records and propose `revisions[]` entries.

Proposals are returned, never written: a person reviews each one and adds it
to the record.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..records import Record


def normalize_url(url: str) -> str:
    """A comparison key: https, lower-case host without `www.` or default port, no fragment
    or trailing slash."""
    parts = urllib.parse.urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urllib.parse.urlunsplit(("https", host, path, parts.query, ""))


@dataclass
class DocumentMatch:
    record: Record
    via: str  # "source" or "revision"


@dataclass
class RevisionProposal:
    document_id: str
    path: str  # relative to data/
    via: str
    revision: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "path": self.path,
            "via": self.via,
            "revision": self.revision,
        }


def _record_urls(record: Record) -> Iterable[tuple[str, str]]:
    for source in record.data.get("sources") or []:
        if isinstance(source, dict):
            for key in ("url", "archived_url"):
                if source.get(key):
                    yield source[key], "source"
    for revision in record.data.get("revisions") or []:
        if isinstance(revision, dict):
            for key in ("url", "archived_url"):
                if revision.get(key):
                    yield revision[key], "revision"


def known_urls(documents: Iterable[Record]) -> set[str]:
    """Normalized URL of every source and revision across the documents."""
    return {normalize_url(u) for record in documents for u, _ in _record_urls(record)}


def find_documents_for_url(url: str, documents: Iterable[Record]) -> list[DocumentMatch]:
    """Document records whose sources[] or revisions[] cite `url` (revision matches win)."""
    key = normalize_url(url)
    matches: list[DocumentMatch] = []
    for record in documents:
        vias = {via for u, via in _record_urls(record) if normalize_url(u) == key}
        if vias:
            matches.append(DocumentMatch(record, "revision" if "revision" in vias else "source"))
    return matches


def propose_revisions(
    change: dict[str, Any], documents: Iterable[Record]
) -> list[RevisionProposal]:
    """Proposed `revisions[]` entries for the documents that cite a changed URL.

    `change` is a change entry (see report.ChangeEntry.to_dict()). The sha256
    is included only when it hashes extracted text, as the schema defines it;
    a bytes-only hash is mentioned in the note instead. A document whose most
    recent revision already carries the new hash gets no proposal.
    """
    proposals: list[RevisionProposal] = []
    new_hash = change.get("new_hash")
    text_hash = change.get("hash_basis", "text") == "text"
    for match in find_documents_for_url(change["url"], documents):
        revisions = match.record.data.get("revisions") or []
        if (
            revisions
            and isinstance(revisions[-1], dict)
            and revisions[-1].get("sha256") == new_hash
        ):
            continue
        revision: dict[str, Any] = {"observed": change["observed"], "url": change["url"]}
        if text_hash and new_hash:
            revision["sha256"] = new_hash
            note = "Change watcher observed a change in the page text"
        else:
            note = f"Change watcher observed a change in the file (bytes sha256 {new_hash})"
        if change.get("old_hash"):
            note += f"; previous hash {change['old_hash'][:12]}"
        revision["note"] = note + ". Review the text before recording what changed."
        proposals.append(
            RevisionProposal(match.record.id or "", match.record.rel, match.via, revision)
        )
    return proposals
