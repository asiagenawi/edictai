"""Federal candidates in the classification stages.

Rows come from `statescope federal` (Congress.gov bills and Federal Register
documents) and go through the same triage and full stages as state bills,
with their own batch lines:

- bills (`source: congress`) are keyed `us-{congress}-{type}-{number}`, the
  id of the legislation record they would become;
- executive orders are keyed `us-eo-{number}` (an executive-action record);
- other Federal Register documents (rules, proposed rules, notices) are keyed
  `us-fr-{document number}` (a document record).

Which records already exist is worked out again from `data/` each time the
candidates are loaded, so a bill recorded since the search ran is not batched.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ..federal.existing import build_index
from ..ingest.candidates import match_keywords, trigger_strength
from .texts import TextStore

LEGISLATION = "legislation"
EXECUTIVE_ACTION = "executive-action"
DOCUMENT = "document"

DESCRIPTION_MAX = 700
# Full stage: CRS summaries of omnibus bills run to hundreds of thousands of characters.
SUMMARY_FULL_MAX = 3000
SUMMARY_PASSAGES = 8
SUMMARY_RADIUS = 300
SPONSORS_MAX = 25
HISTORY_MAX = 60

# Text version types in the order a bill moves through them, for versions
# Congress.gov lists without a date (an enrolled bill, usually).
_STAGE = (
    ("Public Law", 9),
    ("Enrolled", 8),
    ("Engrossed Amendment", 7),
    ("Agreed to", 6),
    ("Engrossed", 5),
    ("Received", 4),
    ("Referred", 4),
    ("Placed on Calendar", 3),
    ("Reported", 3),
    ("Printed as Passed", 5),
    ("Introduced", 1),
)


def record_kind(key_or_row: str | Mapping[str, Any]) -> str:
    """legislation, executive-action, or document."""
    if isinstance(key_or_row, Mapping):
        if key_or_row.get("source") == "congress":
            return LEGISLATION
        return EXECUTIVE_ACTION if key_or_row.get("executive_order_number") else DOCUMENT
    if key_or_row.startswith("us-eo-"):
        return EXECUTIVE_ACTION
    if key_or_row.startswith("us-fr-"):
        return DOCUMENT
    return LEGISLATION


def refresh_existing(candidates: dict[str, dict[str, Any]], data_dir: Path) -> int:
    """Set `existing_record` on federal rows from the records now in data/.

    Returns how many rows gained a match the candidate file did not have.
    """
    index = build_index(data_dir)
    added = 0
    for row in candidates.values():
        if row.get("source") == "congress":
            rid = index.match_bill(row["congress"], row["type"], row["number"])
        elif row.get("source") == "federal_register":
            rid = index.match_document(
                row.get("document_number"), row.get("executive_order_number")
            )
        else:
            continue
        if rid and not row.get("existing_record"):
            added += 1
        if rid:
            row["existing_record"] = rid
            row["existing"] = True
    return added


# -- bill text versions ---------------------------------------------------------


def _stage(version_type: str | None) -> int:
    for prefix, rank in _STAGE:
        if (version_type or "").startswith(prefix):
            return rank
    return 2


def order_versions(versions: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Text versions oldest first. An undated version is placed by its stage after the
    dated versions of earlier stages (Congress.gov leaves enrolled bills undated)."""
    rows = [dict(v) for v in versions]
    dated = [r for r in rows if r.get("date")]

    def when(r: Mapping[str, Any]) -> str:
        if r.get("date"):
            return str(r["date"])
        before = [str(d["date"]) for d in dated if _stage(d.get("type")) <= _stage(r.get("type"))]
        return max(before, default="")

    return sorted(rows, key=lambda r: (when(r), _stage(r.get("type"))))


def version_id(version: Mapping[str, Any]) -> str | None:
    """The document file stem, e.g. 'BILLS-119hr5351ih' or 'PLAW-118publ50'."""
    url = version.get("url") or version.get("pdf_url") or version.get("xml_url")
    if not url:
        return None
    m = re.search(r"/([A-Za-z]+-[0-9A-Za-z]+)\.(?:htm|html|pdf|xml)$", url)
    return m.group(1) if m else None


# -- triage ---------------------------------------------------------------------


def _cut(text: str | None, limit: int = DESCRIPTION_MAX) -> tuple[str | None, str | None]:
    """(text shortened to `limit`, the part cut off) for a long description."""
    if not text:
        return None, None
    if len(text) <= limit:
        return text, None
    return text[: limit - 3].rsplit(" ", 1)[0] + "...", text[limit - 100 :]


def _bill_status(row: Mapping[str, Any]) -> str:
    latest = row.get("latest_action") or {}
    if latest.get("date"):
        return f"last action {latest['date']}: {latest.get('text')}"
    return "Unknown"


def _document_type(row: Mapping[str, Any]) -> str:
    return row.get("subtype") or row.get("type") or "Document"


def document_identifier(row: Mapping[str, Any]) -> str:
    if row.get("executive_order_number"):
        return f"EO {row['executive_order_number']}"
    return f"FR Doc. {row['document_number']}"


def _document_status(row: Mapping[str, Any]) -> str:
    parts = [f"published {row.get('publication_date')}"]
    if row.get("signing_date"):
        parts.append(f"signed {row['signing_date']}")
    if row.get("effective_on"):
        parts.append(f"effective {row['effective_on']}")
    return "; ".join(parts)


def triage_item(row: Mapping[str, Any], store: TextStore | None = None) -> dict[str, Any]:
    from .prep import snippets

    if row.get("source") == "congress":
        description, rest = _cut(row.get("summary"))
        keywords = row.get("matched_keywords") or []
        official = row.get("official_title")
        item: dict[str, Any] = {
            "key": row["key"],
            "jurisdiction": "us",
            "session": str(row["congress"]),
            "identifier": row["identifier"],
            "bill_type": row.get("type"),
            "title": row.get("title"),
            "official_title": official if official != row.get("title") else None,
            "description": description,
            "policy_area": row.get("policy_area"),
            "status": _bill_status(row),
            "matched_in": row.get("matched_in") or [],
            "trigger_strength": "weak" if trigger_strength(keywords) == "weak" else None,
            "matched_keywords": keywords,
        }
        sources: list[str | None] = [rest]
    else:
        description, rest = _cut(row.get("abstract"))
        keywords = match_keywords(row.get("title"), row.get("abstract"))
        item = {
            "key": row["key"],
            "jurisdiction": "us",
            "identifier": document_identifier(row),
            "record_type": record_kind(row),
            "document_type": _document_type(row),
            "agency": row.get("agency"),
            "title": row.get("title"),
            "description": description,
            "status": _document_status(row),
            "matched_by": "fulltext",
            "matched_keywords": keywords,
        }
        sources = [rest]
    if store is not None:
        _, text = store.for_key(row["key"])
        if text:
            sources.insert(0, text)
            keywords = keywords or match_keywords(text)
    item["snippets"] = snippets(sources, keywords)
    return {k: v for k, v in item.items() if v not in (None, [], "")} | {"key": row["key"]}


# -- full stage -------------------------------------------------------------------


def actions(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The bill's dated actions, oldest first, each event once.

    Congress.gov often lists one event twice (from the chamber and from the
    Library of Congress); those share a date and text and are merged.
    """
    seen: set[tuple[str, str]] = set()
    out = []
    for a in sorted(row.get("actions") or [], key=lambda a: a.get("date") or ""):
        if not a.get("date") or not a.get("text"):
            continue
        sig = (a["date"], re.sub(r"\s+", " ", a["text"]).strip().rstrip(".").lower())
        if sig in seen:
            continue
        seen.add(sig)
        out.append(a)
    return out


def _history(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = [
        {"date": a["date"], "chamber": a.get("actor"), "action": a["text"]} for a in actions(row)
    ]
    if len(history) > HISTORY_MAX:
        history = [
            *history[:10],
            {"note": f"{len(history) - HISTORY_MAX} actions omitted"},
            *history[-(HISTORY_MAX - 10) :],
        ]
    return history


def _sponsors(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    people = [
        {
            "name": s.get("name"),
            "party": s.get("party") or None,
            "role": s.get("role"),
            "primary": s.get("role") == "sponsor",
        }
        for s in row.get("sponsors") or []
        if not s.get("withdrawn_date")
    ]
    if len(people) > SPONSORS_MAX:
        rest = len(people) - SPONSORS_MAX
        people = [*people[:SPONSORS_MAX], {"note": f"{rest} more cosponsors"}]
    return people


def _versions(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"type": v.get("type"), "date": v.get("date"), "url": v.get("url") or v.get("pdf_url")}
        for v in order_versions(row.get("texts") or [])
    ]


def summary_view(summary: str | None) -> dict[str, Any]:
    """`description` for a full-stage line; a long summary is cut, and the passages of it
    that mention AI are listed under `description_ai_passages`."""
    from .prep import _ai_hits
    from .textutil import collapse

    if not summary or len(summary) <= SUMMARY_FULL_MAX:
        return {"description": summary}
    head = summary[: SUMMARY_FULL_MAX - 3].rsplit(" ", 1)[0] + "..."
    flat = collapse(summary)
    spans: list[list[int]] = []
    for start, end, _name in _ai_hits(flat):
        if start < len(collapse(head)) - 3:
            continue
        a, b = max(0, start - SUMMARY_RADIUS), min(len(flat), end + SUMMARY_RADIUS)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    passages = [
        ("..." if a else "") + flat[a:b] + ("..." if b < len(flat) else "")
        for a, b in spans[:SUMMARY_PASSAGES]
    ]
    out: dict[str, Any] = {"description": head}
    if passages:
        out["description_ai_passages"] = passages
    if len(spans) > SUMMARY_PASSAGES:
        out["description_ai_passages_omitted"] = len(spans) - SUMMARY_PASSAGES
    return out


def full_item(
    row: Mapping[str, Any],
    store: TextStore,
    triage: Mapping[str, Any] | None = None,
    *,
    excerpt_cap: int,
) -> dict[str, Any]:
    from .prep import text_view

    if row.get("source") == "congress":
        versions = _versions(row)
        latest = versions[-1] if versions else None
        item: dict[str, Any] = {
            "key": row["key"],
            "jurisdiction": "us",
            "session": str(row["congress"]),
            "identifier": row["identifier"],
            "bill_type": row.get("type"),
            "title": row.get("title"),
            "official_title": row.get("official_title"),
            **summary_view(row.get("summary")),
            "policy_area": row.get("policy_area"),
            "status": _bill_status(row),
            "url": row.get("url"),
            "sponsors": _sponsors(row),
            "history": _history(row),
            "versions": versions,
            "triage": dict(triage) if triage else None,
            "text": text_view(
                store,
                row["key"],
                excerpt_cap,
                fallback={"type": latest["type"], "date": latest["date"]} if latest else None,
            ),
        }
    else:
        item = {
            "key": row["key"],
            "jurisdiction": "us",
            "identifier": document_identifier(row),
            "record_type": record_kind(row),
            "document_type": _document_type(row),
            "agency": row.get("agency"),
            "title": row.get("title"),
            "description": row.get("abstract"),
            "status": _document_status(row),
            "citation": row.get("citation"),
            "docket_ids": row.get("docket_ids") or None,
            "url": row.get("html_url"),
            "triage": dict(triage) if triage else None,
            "text": text_view(store, row["key"], excerpt_cap),
        }
    return {k: v for k, v in item.items() if v is not None}
