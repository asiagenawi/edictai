"""Write the batch files reviewers work through.

`triage` batches carry compact metadata; `full` batches add the bill's
history, sponsors, versions, and the parts of its text around AI terms.
A key already placed in a batch of the same stage is not placed again
(unless `rebuild` is set), so re-running prep after new candidates arrive
only adds batches for the new bills.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..ingest.candidates import KEYWORDS, match_keywords
from .common import (
    BillStore,
    ClassifyPaths,
    latest_text,
    read_jsonl,
    real_date,
    write_json,
    write_jsonl,
)
from .texts import TextStore
from .textutil import collapse

DESCRIPTION_MAX = 700
SNIPPET_RADIUS = 110
EXCERPT_CAP = 6000
SEGMENT = 900
HEAD = 450
HISTORY_MAX = 60

EDUCATION = re.compile(
    r"\b(?:schools?|students?|pupils?|teachers?|educators?|faculty|school districts?|"
    r"local education(?:al)? agenc(?:y|ies)|boards? of education|department of education|"
    r"education|universit(?:y|ies)|colleges?|institutions? of higher (?:education|learning)|"
    r"higher education|community colleges?|curricul(?:um|a)|instruction(?:al)?|"
    r"classrooms?|charter schools?|K-12|kindergarten|postsecondary)\b",
    re.IGNORECASE,
)
HEADING = re.compile(
    r"(?m)(?:^|NEW SECTION\.\s+)(?:(?:Sec|SEC|Section|SECTION)\.?\s*\d[\w.\-]*(?:\([0-9a-z\-]+\))*\.?"
    r"|\u00a7+\s*\d[\w.\-:]*|(?:Article|ARTICLE|Part|PART)\s+[0-9IVXL]+\b"
    r"|\(\d+ ILCS [^)]+\)|\d{3,6}(?:\.\d+)+\.(?=\s))"
)
PAGE = re.compile(r"\[Page \d+\]")


def _ai_hits(text: str) -> list[tuple[int, int, str]]:
    hits = []
    for name, pattern in KEYWORDS.items():
        hits.extend((m.start(), m.end(), name) for m in pattern.finditer(text))
    return sorted(hits)


def snippets(texts: Iterable[str | None], keywords: Iterable[str], limit: int = 2) -> list[str]:
    """Short passages around the first matches of the given keywords."""
    out: list[str] = []
    wanted = [KEYWORDS[k] for k in keywords if k in KEYWORDS]
    for text in texts:
        if not text:
            continue
        flat = collapse(text)
        for pattern in wanted:
            m = pattern.search(flat)
            if not m:
                continue
            start = max(0, m.start() - SNIPPET_RADIUS)
            end = min(len(flat), m.end() + SNIPPET_RADIUS)
            piece = flat[start:end]
            piece = ("..." if start else "") + piece + ("..." if end < len(flat) else "")
            if piece not in out and not any(piece[3:40] in o for o in out):
                out.append(piece)
            if len(out) >= limit:
                return out
    return out


def _status(row: Mapping[str, Any]) -> str:
    history = row.get("history") or []
    last = history[-1] if history else {}
    parts = [row.get("status_text") or "Unknown"]
    if last:
        parts.append(f"last action {last.get('date')}: {last.get('action')}")
    return "; ".join(str(p) for p in parts)


def triage_item(row: Mapping[str, Any], store: TextStore | None = None) -> dict[str, Any]:
    description = row.get("description") or ""
    if description.strip() == (row.get("title") or "").strip():
        description = ""
    keywords = row.get("matched_keywords") or []
    # Snippets show matches the item would not otherwise show: in a long
    # description past the cut, or in bill text already fetched.
    sources: list[str | None] = []
    if len(description) > DESCRIPTION_MAX:
        sources.append(description[DESCRIPTION_MAX - 100 :])
        description = description[: DESCRIPTION_MAX - 3].rsplit(" ", 1)[0] + "..."
    if store is not None:
        _, text = store.for_key(row["key"])
        if text:
            sources.insert(0, text)
            keywords = keywords or match_keywords(text)
    item = {
        "key": row["key"],
        "jurisdiction": row["jurisdiction"],
        "session": row["session"],
        "identifier": row["identifier"],
        "bill_type": row.get("bill_type"),
        "title": row.get("title"),
        "description": description or None,
        "status": _status(row),
        "matched_by": row.get("matched_by"),
        "trigger_strength": row.get("trigger_strength"),
        "matched_keywords": row.get("matched_keywords") or [],
        "fulltext_queries": row.get("fulltext_queries") or [],
        "snippets": snippets(sources, keywords),
    }
    return {k: v for k, v in item.items() if v not in (None, [], "")} | {"key": row["key"]}


# -- excerpts -----------------------------------------------------------------


@dataclass
class Excerpts:
    excerpts: list[dict[str, Any]] = field(default_factory=list)
    ai_hits: int = 0
    education_hits: int = 0
    excerpt_chars: int = 0
    truncated: bool = False  # some passages around AI terms were cut for length
    complete: bool = False  # the excerpts are the whole text


def _segments(text: str, size: int = SEGMENT) -> list[tuple[int, int]]:
    """(start, end) spans of about `size` characters, broken at line or sentence ends."""
    spans: list[tuple[int, int]] = []
    start = 0
    n = len(text)
    while start < n:
        # The opening span (title and enacting clause) is kept short.
        limit = HEAD if not spans else size
        end = min(n, start + limit)
        if end < n:
            floor = start + limit // 2
            cut = text.rfind("\n", floor, end)
            if cut == -1:
                cut = max(text.rfind(". ", floor, end), text.rfind("; ", floor, end))
                cut = cut + 1 if cut != -1 else -1
            if cut == -1:
                cut = text.rfind(" ", floor, end)
            end = cut if cut > start else end
        spans.append((start, end))
        start = end
    return spans


def _heading_before(pos: int, headings: list[tuple[int, str]]) -> str | None:
    best = None
    for at, label in headings:
        if at > pos:
            break
        best = label
    return best


def excerpts(text: str, cap: int = EXCERPT_CAP) -> Excerpts:
    """The parts of the text around AI terms, preferring those that also mention education.

    The text is cut into segments of about SEGMENT characters. The opening
    segment (title and enacting clause) is always kept; the rest are ranked
    by AI-term hits, with a large bonus for segments that also name schools,
    students, educators, or institutions, and kept until `cap` characters.
    Adjacent kept segments are joined, and each excerpt carries the nearest
    section heading above its first AI mention.
    """
    result = Excerpts()
    hits = [h[0] for h in _ai_hits(text)]
    edu = [m.start() for m in EDUCATION.finditer(text)]
    result.ai_hits, result.education_hits = len(hits), len(edu)
    spans = _segments(text)
    sizes = [len(collapse(text[a:b])) for a, b in spans]

    def score(i: int) -> tuple[int, int]:
        a, b = spans[i]
        ai = sum(1 for h in hits if a <= h < b)
        ed = sum(1 for p in edu if a <= p < b)
        if i == 0:
            return (3, 0)
        if ai and ed:
            return (2, ai + ed)
        if ai or not hits:
            return (1, ai + ed)
        return (0, ed)

    ranked = sorted(range(len(spans)), key=lambda i: (score(i), -i), reverse=True)
    chosen: set[int] = set()
    used = 0
    for i in ranked:
        if used + sizes[i] > cap:
            if score(i)[0] > 0:
                result.truncated = True
            continue
        chosen.add(i)
        used += sizes[i]
    headings = [(m.start(), collapse(m.group(0))) for m in HEADING.finditer(text)]
    pages = [(m.start(), m.group(0)) for m in PAGE.finditer(text)]
    groups: list[list[int]] = []
    for i in sorted(chosen):
        if groups and groups[-1][-1] == i - 1:
            groups[-1].append(i)
        else:
            groups.append([i])
    for n, group in enumerate(groups, 1):
        a, b = spans[group[0]][0], spans[group[-1]][1]
        entry: dict[str, Any] = {"n": n}
        # The heading that governs the excerpt's first AI mention, else its start.
        anchor = next((h for h in hits if a <= h < b), a)
        heading = _heading_before(anchor, headings) if anchor else None
        if heading:
            entry["heading"] = heading
        page = _heading_before(a + 1, pages)
        if page:
            entry["page"] = page
        entry["text"] = collapse(text[a:b])
        result.excerpts.append(entry)
    result.excerpt_chars = used
    result.complete = len(chosen) == len(spans)
    return result


def _excerpt_fields(text: str, cap: int) -> dict[str, Any]:
    ex = excerpts(text, cap)
    return {
        "chars": len(collapse(text)),
        "excerpt_chars": ex.excerpt_chars,
        "complete": ex.complete,
        "truncated": ex.truncated,
        "ai_hits": ex.ai_hits,
        "education_hits": ex.education_hits,
        "excerpts": ex.excerpts,
    }


def full_item(
    row: Mapping[str, Any],
    bill: Mapping[str, Any] | None,
    store: TextStore,
    triage: Mapping[str, Any] | None = None,
    *,
    excerpt_cap: int = EXCERPT_CAP,
) -> dict[str, Any]:
    """One full-stage batch line. An earlier version stored for the bill goes under
    `text.earlier`, with its own excerpts (each version gets `excerpt_cap`)."""
    source = bill or row
    meta, text = store.for_key(row["key"])
    history = [
        {"date": h.get("date"), "chamber": h.get("chamber"), "action": h.get("action")}
        for h in sorted(source.get("history") or [], key=lambda h: h.get("date") or "")
    ]
    if len(history) > HISTORY_MAX:
        history = [
            *history[:10],
            {"note": f"{len(history) - HISTORY_MAX} actions omitted"},
            *history[-(HISTORY_MAX - 10) :],
        ]
    sponsors = [
        {
            "name": s.get("name"),
            "party": s.get("party") or None,
            "role": s.get("role") or None,
            "primary": s.get("sponsor_type_id") == 1,
        }
        for s in source.get("sponsors") or []
    ]
    versions = [
        {
            "type": t.get("type"),
            "date": real_date(t.get("date")),
            "url": t.get("state_link") or None,
            "mime": t.get("mime"),
        }
        for t in sorted(
            source.get("texts") or [], key=lambda t: (t.get("date") or "", t.get("doc_id") or 0)
        )
    ]
    latest = latest_text(source.get("texts") or [])
    text_block: dict[str, Any] = {"status": (meta or {}).get("status", "not_fetched")}
    if meta:
        text_block.update(
            {
                "type": meta.get("type"),
                "date": meta.get("date"),
                "url": meta.get("state_link"),
            }
        )
        if meta.get("reason"):
            text_block["reason"] = meta["reason"]
    elif latest:
        text_block.update({"type": latest.get("type"), "date": latest.get("date")})
    if text:
        text_block.update(_excerpt_fields(text, excerpt_cap))
    earlier_meta, earlier_text = store.earlier_for_key(row["key"])
    if earlier_meta and earlier_text:
        text_block["earlier"] = {
            "type": earlier_meta.get("type"),
            "date": earlier_meta.get("date"),
            "url": earlier_meta.get("state_link"),
            **_excerpt_fields(earlier_text, excerpt_cap),
        }
    item = {
        "key": row["key"],
        "jurisdiction": row["jurisdiction"],
        "session": row["session"],
        "identifier": row["identifier"],
        "bill_type": source.get("bill_type"),
        "title": source.get("title"),
        "description": source.get("description"),
        "status": _status(row),
        "url": source.get("state_link") or row.get("url"),
        "sponsors": sponsors,
        "history": history,
        "versions": versions,
        "triage": dict(triage) if triage else None,
        "text": text_block,
    }
    return {k: v for k, v in item.items() if v is not None}


# -- batching -----------------------------------------------------------------


def assigned_keys(stage_dir: Path) -> set[str]:
    keys: set[str] = set()
    for path in stage_dir.glob("batch-*.jsonl"):
        keys.update(r["key"] for r in read_jsonl(path))
    return keys


def judged_keys(out_dir: Path) -> set[str]:
    keys: set[str] = set()
    for path in out_dir.glob("*.jsonl"):
        try:
            keys.update(r["key"] for r in read_jsonl(path) if isinstance(r.get("key"), str))
        except ValueError:
            continue
    return keys


def _next_number(stage_dir: Path) -> int:
    numbers = [
        int(m.group(1))
        for p in [*stage_dir.glob("batch-*.jsonl"), *(stage_dir / "out").glob("batch-*.jsonl")]
        if (m := re.fullmatch(r"batch-(\d+)", p.stem))
    ]
    return max(numbers, default=0) + 1


@dataclass
class PrepResult:
    stage: str
    batches: list[Path] = field(default_factory=list)
    items: int = 0
    skipped_existing: int = 0
    skipped_assigned: int = 0
    skipped_judged: int = 0
    without_text: list[str] = field(default_factory=list)
    unknown_keys: list[str] = field(default_factory=list)


def write_batches(
    cp: ClassifyPaths,
    stage: str,
    items: list[dict[str, Any]],
    batch_size: int,
    result: PrepResult,
) -> None:
    stage_dir = cp.stage_dir(stage)
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "out").mkdir(exist_ok=True)
    number = _next_number(stage_dir)
    for i in range(0, len(items), batch_size):
        chunk = items[i : i + batch_size]
        path = stage_dir / f"batch-{number:03d}.jsonl"
        write_jsonl(path, chunk)
        result.batches.append(path)
        number += 1
    result.items = len(items)
    manifest = []
    for path in sorted(stage_dir.glob("batch-*.jsonl")):
        out = stage_dir / "out" / path.name
        manifest.append(
            {
                "batch": _show(path, cp.paths.root),
                "output": _show(out, cp.paths.root),
                "items": sum(1 for _ in path.open(encoding="utf-8")),
                "done": out.exists(),
            }
        )
    write_json(stage_dir / "manifest.json", manifest)


def _show(path: Path, root: Path) -> str:
    """The path relative to the repository when inside it, else absolute."""
    rel = os.path.relpath(path, root)
    return str(path) if rel.startswith("..") else rel


def clear_batches(stage_dir: Path) -> None:
    """Remove the batch files that have no judgments yet."""
    for path in stage_dir.glob("batch-*.jsonl"):
        if not (stage_dir / "out" / path.name).exists():
            path.unlink()


def prep_triage(
    cp: ClassifyPaths,
    candidates: Mapping[str, Mapping[str, Any]],
    *,
    batch_size: int,
    keys: Iterable[str] | None = None,
    rebuild: bool = False,
) -> PrepResult:
    result = PrepResult("triage")
    stage_dir = cp.stage_dir("triage")
    if rebuild:
        clear_batches(stage_dir)
    assigned = assigned_keys(stage_dir)
    judged = judged_keys(cp.out_dir("triage"))
    store = TextStore(cp.texts)
    wanted = list(keys) if keys is not None else sorted(candidates)
    items = []
    for key in wanted:
        row = candidates.get(key)
        if row is None:
            result.unknown_keys.append(key)
            continue
        if row.get("existing_record"):
            result.skipped_existing += 1
            continue
        if key in judged:
            result.skipped_judged += 1
            continue
        if key in assigned:
            result.skipped_assigned += 1
            continue
        items.append(triage_item(row, store))
    write_batches(cp, "triage", items, batch_size, result)
    return result


def triage_decisions(cp: ClassifyPaths) -> dict[str, dict[str, Any]]:
    """key -> the last triage judgment recorded for it."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(cp.out_dir("triage").glob("*.jsonl")):
        for row in read_jsonl(path):
            if isinstance(row.get("key"), str):
                out[row["key"]] = row
    return out


def prep_full(
    cp: ClassifyPaths,
    candidates: Mapping[str, Mapping[str, Any]],
    *,
    batch_size: int,
    keys: Iterable[str] | None = None,
    rebuild: bool = False,
    require_text: bool = False,
    excerpt_cap: int = EXCERPT_CAP,
    rejudge: bool = False,
) -> PrepResult:
    """Batch bills for the full stage.

    With `rejudge`, the given keys are batched again even when already judged
    or in a batch (for re-reading them with fuller text, e.g. a larger
    `excerpt_cap`); the new judgments replace the old ones once checked.
    """
    result = PrepResult("full")
    stage_dir = cp.stage_dir("full")
    if rebuild:
        clear_batches(stage_dir)
    assigned = assigned_keys(stage_dir)
    judged = judged_keys(cp.out_dir("full"))
    decisions = triage_decisions(cp)
    if keys is None:
        keys = [
            k for k, d in sorted(decisions.items()) if d.get("decision") in ("in", "needs_text")
        ]
    store = TextStore(cp.texts)
    bills = BillStore(cp.legiscan)
    items = []
    try:
        for key in keys:
            row = candidates.get(key)
            if row is None:
                result.unknown_keys.append(key)
                continue
            if row.get("existing_record"):
                result.skipped_existing += 1
                continue
            if key in judged and not rejudge:
                result.skipped_judged += 1
                continue
            if key in assigned and not rejudge:
                result.skipped_assigned += 1
                continue
            meta, _text = store.for_key(key)
            if meta is None:
                result.without_text.append(key)
                if require_text:
                    continue
            triage = decisions.get(key)
            triage_view = (
                {"decision": triage["decision"], "reason": triage.get("reason")} if triage else None
            )
            items.append(
                full_item(
                    row,
                    bills.get(int(row["bill_id"])),
                    store,
                    triage_view,
                    excerpt_cap=excerpt_cap,
                )
            )
    finally:
        bills.close()
    write_batches(cp, "full", items, batch_size, result)
    return result
