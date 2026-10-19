"""Choosing what the week's article is about.

A subject is one of:

- `jurisdiction:<id>`: a State brief on one state's records;
- `provision:<code>`: an Explainer or Analysis on one provision type across states;
- `research:<topic>`: a Research digest of studies on one topic;
- `roundup:<start>..<end>`: a Roundup of what the records show for a period.

Subjects already covered are left out: those in the ledger, and those inferred from
the existing articles' notes (a Roundup covers the days up to the day before it was
published; an article whose notes mostly cite one jurisdiction covers that
jurisdiction, otherwise the provision type its notes cite most; a Research digest
covers the topic its studies share most, and its studies count as covered).

Among the rest, subjects touching this week's changes come first; then article types
in rotation, the least recently used first; then the subject with the most changes,
then the largest. Opinion is never chosen: it is for outside contributors.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .data import Article, Dataset, Record

TYPES = ("Explainer", "Analysis", "State brief", "Research digest", "Roundup")
PROVISION_TYPES = ("Explainer", "Analysis")


@dataclass(frozen=True)
class Limits:
    min_records: int = 6  # provision-type and state subjects
    min_jurisdictions: int = 4  # provision-type subjects
    min_studies: int = 4  # research subjects, not cited by any article
    min_roundup_items: int = 6
    roundup_days: int = 28  # a roundup is due once this many days have passed


@dataclass
class Subject:
    key: str
    type: str
    label: str
    records: list[str] = field(default_factory=list)
    studies: list[str] = field(default_factory=list)
    changed: int = 0
    window: tuple[str, str] | None = None

    @property
    def size(self) -> int:
        return len(self.records) + len(self.studies)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "type": self.type,
            "label": self.label,
            "records": len(self.records),
            "studies": len(self.studies),
            "changed": self.changed,
            "window": list(self.window) if self.window else None,
        }


# -- ledger ---------------------------------------------------------------------------


def read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def ledger_entry(path: Path, run_date: str) -> dict[str, Any] | None:
    return next((e for e in read_ledger(path) if e.get("run_date") == run_date), None)


def write_ledger_entry(path: Path, entry: Mapping[str, Any]) -> None:
    """Add the run's entry, replacing an earlier entry for the same run date."""
    rows = [e for e in read_ledger(path) if e.get("run_date") != entry["run_date"]]
    rows.append(dict(entry))
    rows.sort(key=lambda e: e["run_date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


# -- what is covered ------------------------------------------------------------------


@dataclass
class Coverage:
    keys: set[str] = field(default_factory=set)
    studies: set[str] = field(default_factory=set)
    roundup_end: str | None = None  # last day covered by a roundup
    last_used: dict[str, str] = field(default_factory=dict)  # type -> latest date


def article_subject(ds: Dataset, a: Article) -> str | None:
    if a.type == "Roundup":
        return f"roundup:{a.published}"
    if a.type == "Research digest":
        topics = Counter(t for sid, _ in a.study_notes for t in ds.studies[sid].topics)
        return f"research:{topics.most_common(1)[0][0]}" if topics else None
    recs = [(ds.records[rid], i) for rid, i in a.record_notes if rid in ds.records]
    if not recs:
        return None
    juris = Counter(r.jurisdiction for r, _ in recs)
    top, n = juris.most_common(1)[0]
    if n / len(recs) >= 0.6:
        return f"jurisdiction:{top}"
    kinds = Counter(r.provisions[i].type for r, i in recs if i < len(r.provisions))
    return f"provision:{kinds.most_common(1)[0][0]}" if kinds else None


def coverage(ds: Dataset, articles: Iterable[Article], ledger: Iterable[Mapping]) -> Coverage:
    c = Coverage()

    def used(kind: str, day: str) -> None:
        if day and day > c.last_used.get(kind, ""):
            c.last_used[kind] = day

    for a in articles:
        key = article_subject(ds, a)
        if key:
            c.keys.add(key)
        c.studies |= {sid for sid, _ in a.study_notes}
        used(a.type, a.published)
        if a.type == "Roundup" and a.published:
            end = (date.fromisoformat(a.published) - timedelta(days=1)).isoformat()
            c.roundup_end = max(c.roundup_end or "", end)
    for e in ledger:
        if e.get("status") != "written":
            continue
        c.keys.add(e["subject"])
        used(e["type"], e["run_date"])
        window = e.get("window")
        if window:
            c.roundup_end = max(c.roundup_end or "", window[1])
    return c


# -- candidates -----------------------------------------------------------------------


def _priority(r: Record, changed: set[str]) -> tuple:
    settled = r.status in ("enacted", "current") or r.type == "executive-action"
    return (r.id not in changed, not settled, _neg(r.latest()), r.id)


def _neg(day: str) -> int:
    """A key that sorts dates (YYYY, YYYY-MM, or YYYY-MM-DD) newest first; none last."""
    digits = "".join(ch for ch in day if ch.isdigit())
    return -int((digits + "00000000")[:8])


def order_records(ds: Dataset, ids: Iterable[str], changed: set[str]) -> list[str]:
    return sorted(ids, key=lambda i: _priority(ds.records[i], changed))


def candidates(
    ds: Dataset,
    cover: Coverage,
    changed: set[str],
    *,
    run_date: date,
    limits: Limits | None = None,
) -> list[Subject]:
    limits = limits or Limits()
    out: list[Subject] = []
    policy = list(ds.records.values())

    # One state.
    by_state: dict[str, list[str]] = {}
    for r in policy:
        if r.jurisdiction != "us" and r.provisions:
            by_state.setdefault(r.jurisdiction, []).append(r.id)
    for jid, ids in by_state.items():
        key = f"jurisdiction:{jid}"
        if key in cover.keys or len(ids) < limits.min_records:
            continue
        out.append(
            Subject(
                key,
                "State brief",
                f"{ds.jurisdictions.get(jid, jid)}: its records for K-12 and higher education",
                order_records(ds, ids, changed),
                changed=sum(i in changed for i in ids),
            )
        )

    # One provision type across states.
    by_type: dict[str, set[str]] = {}
    for r in policy:
        for p in r.provisions:
            by_type.setdefault(p.type, set()).add(r.id)
    for code, ids in by_type.items():
        key = f"provision:{code}"
        juris = {ds.records[i].jurisdiction for i in ids}
        if (
            key in cover.keys
            or len(ids) < limits.min_records
            or len(juris) < limits.min_jurisdictions
        ):
            continue
        for kind in PROVISION_TYPES:
            out.append(
                Subject(
                    key,
                    kind,
                    f"Records with a provision of type {code} "
                    f"({ds.provision_types.get(code, code)})",
                    order_records(ds, ids, changed),
                    changed=sum(i in changed for i in ids),
                )
            )

    # Studies on one topic that no article cites.
    by_topic: dict[str, list[str]] = {}
    for s in ds.studies.values():
        for t in s.topics:
            by_topic.setdefault(t, []).append(s.id)
    for topic, ids in by_topic.items():
        key = f"research:{topic}"
        fresh = [i for i in ids if i not in cover.studies]
        if key in cover.keys or len(fresh) < limits.min_studies:
            continue
        ids = sorted(ids, key=lambda i: (i in cover.studies, _neg(ds.studies[i].published), i))
        out.append(
            Subject(
                key,
                "Research digest",
                f"Research on {ds.topics.get(topic, topic)}",
                studies=ids,
                changed=sum(i in changed for i in fresh),
            )
        )

    # What happened since the last roundup.
    start = (
        date.fromisoformat(cover.roundup_end) + timedelta(days=1)
        if cover.roundup_end
        else run_date - timedelta(days=limits.roundup_days)
    )
    end = run_date - timedelta(days=1)
    if start <= end:
        lo, hi = start.isoformat(), end.isoformat()
        active = [
            r.id for r in policy if any(lo <= d[:10] <= hi for d, _ in r.activity() if len(d) >= 10)
        ]
        key = f"roundup:{lo}..{hi}"
        if key not in cover.keys and len(active) >= limits.min_roundup_items:
            due = (end - start).days + 1 >= limits.roundup_days
            out.append(
                Subject(
                    key,
                    "Roundup",
                    f"What Edictai's records show from {lo} to {hi}",
                    sorted(active, key=lambda i: (ds.records[i].latest(), i)),
                    changed=sum(i in changed for i in active) if due else 0,
                    window=(lo, hi),
                )
            )
    return out


def rotation(cover: Coverage) -> list[str]:
    """Article types, the least recently used first (never used first of all)."""
    return sorted(TYPES, key=lambda t: (cover.last_used.get(t, ""), TYPES.index(t)))


def choose(
    subjects: list[Subject],
    cover: Coverage,
    *,
    kind: str | None = None,
    key: str | None = None,
) -> Subject | None:
    order = rotation(cover)
    pool = [
        s for s in subjects if (kind is None or s.type == kind) and (key is None or s.key == key)
    ]
    if not pool:
        return None
    return min(
        pool,
        key=lambda s: (s.changed == 0, order.index(s.type), -s.changed, -s.size, s.key),
    )


# -- drawing --------------------------------------------------------------------------

SCENES = {
    "addresses-deepfakes": "phone",
    "chatbot-minor-protections": "phone",
    "restricts-student-data-use": "lock",
    "procurement-standards": "lock",
    "educator-pd-requirement": "classroom",
    "educator-prep-requirement": "classroom",
    "prohibits-ai-replacing-educators": "classroom",
    "limits-ai-instructional-use": "classroom",
    "requires-ai-literacy-instruction": "classroom",
    "ai-in-standards": "classroom",
    "ai-course-or-graduation-requirement": "classroom",
    "academic-integrity-rules": "classroom",
}
TOPIC_SCENES = {"student-privacy": "lock", "data-governance": "lock", "synthetic-media": "phone"}


def default_drawing(ds: Dataset, a: Article) -> str:
    """The drawing the site gives an article that names none (site/src/lib/articles.ts)."""
    if a.drawing:
        return a.drawing
    if a.type == "Opinion":
        return "op"
    if a.type == "Roundup":
        return "calendar"
    if a.type == "Research digest":
        return "desk"
    juris = {ds.records[r].jurisdiction for r, _ in a.record_notes if r in ds.records}
    if any(j != "us" for j in juris):
        return "map"
    return "capitol" if juris == {"us"} else "desk"


def choose_drawing(ds: Dataset, subject: Subject, articles: Iterable[Article]) -> str:
    """The scene that fits the subject, avoiding the drawings of the two newest articles
    when another fitting scene is free."""
    kind, _, value = subject.key.partition(":")
    recs = [ds.records[i] for i in subject.records if i in ds.records]
    federal = bool(recs) and sum(r.jurisdiction == "us" for r in recs) / len(recs) >= 0.6
    if kind == "roundup":
        fits = ["calendar"]
    elif kind == "research":
        fits = ["desk", "classroom"]
    elif kind == "jurisdiction":
        fits = ["capitol"] if value == "us" else ["gavel", "map"]
    else:
        scene = SCENES.get(value)
        if scene is None:
            topics = Counter(t for r in recs for t in r.topics)
            scene = next(
                (TOPIC_SCENES[t] for t, _ in topics.most_common(3) if t in TOPIC_SCENES), None
            )
        fits = (["capitol"] if federal else []) + ([scene] if scene else []) + ["map", "gavel"]
    newest = sorted(articles, key=lambda a: (a.published, a.slug), reverse=True)[:2]
    recent = {default_drawing(ds, a) for a in newest}
    return next((d for d in fits if d not in recent), fits[0])
