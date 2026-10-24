"""Choosing what the week's article is about.

A subject is one of:

- `jurisdiction:<id>`: a State brief on one state's records, its recent laws first;
- `provision:<code>`: an Explainer or Analysis on one provision type across states;
- `federal:<record id>`: an Explainer on one recent federal action (an order, a
  current document such as a rule or notice, or a bill that has passed a chamber),
  with the federal records that share its provision types;
- `research:<topic>`: a Research digest of studies on one topic;
- `roundup:<start>..<end>`: a Roundup of what the records show for a period.

Subjects already covered are left out: those in the ledger, and those inferred from
the existing articles' notes (a Roundup covers the days up to the day before it was
published; an article whose notes mostly cite one jurisdiction covers that
jurisdiction, otherwise the provision type its notes cite most; a Research digest
covers the topic its studies share most, and its studies count as covered). A
federal action is covered once an article published on or after its latest
activity cites it.

A subject keeps only its most significant records (`significance`), at most
`max_subject_records` of them (and `max_subject_studies` studies), so that a piece
stays focused; the facts block says what the selection covers.

Each subject gets a score, the sum of named parts weighted by `Scoring`:

- records changed this week;
- laws enacted, documents published and orders signed within `recent_days`, and
  the number of jurisdictions they come from;
- records with any activity within `active_days`;
- settled records (enacted, current, in force) at any time, a small weight;
- for research, studies no article cites and studies published within `recent_days`;
- a bonus for a federal action and for a Roundup that is due.

Every count is capped at `count_cap`, so a subject does not win on size alone. A
broad provision type (`broad`: funding, task forces and studies) has its score cut
to `broad_factor` of itself unless `broad_changed` of its records changed this week.
`choose` adds a rotation bonus (the least recently used article type the most) and
takes the highest total; `Subject.why` lists the parts. Opinion is never chosen: it
is for outside contributors.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .data import Article, Dataset, Record

TYPES = ("Explainer", "Analysis", "State brief", "Research digest", "Roundup")
PROVISION_TYPES = ("Explainer", "Analysis")
SETTLED = {"legislation": "enacted", "document": "current", "executive-action": "active"}
SETTLED_DATES = {
    "legislation": ("enacted_date", "status_date"),
    "document": ("published", "updated"),
    "executive-action": ("signed",),
}
ADVANCED = ("passed-legislature", "passed-chamber")
CLOSED = ("failed", "vetoed", "superseded", "withdrawn", "rescinded", "expired")


@dataclass(frozen=True)
class Limits:
    min_records: int = 6  # provision-type and state subjects
    min_jurisdictions: int = 4  # provision-type subjects
    min_studies: int = 4  # research subjects, not cited by any article
    min_roundup_items: int = 6
    roundup_days: int = 28  # a roundup is due once this many days have passed
    max_subject_records: int = 25  # the most significant records a subject keeps
    max_subject_studies: int = 12
    min_federal_records: int = 3  # a federal action with its related federal records


@dataclass(frozen=True)
class Scoring:
    """Weights for choosing a subject (`articles.scoring` in pipeline/automation.yaml)."""

    recent_days: int = 120  # enacted, published or signed this recently counts as recent
    active_days: int = 30  # any dated activity this recently counts as active
    count_cap: int = 6  # each count stops adding beyond this
    changed: float = 8.0  # per record or study changed this week
    enacted_recent: float = 4.0  # per law enacted, document published, order signed recently
    enacted_lately: float = 4.0  # more per one of those within active_days
    jurisdictions_recent: float = 3.0  # per jurisdiction those recent ones come from
    active: float = 1.0  # per record active within active_days
    settled: float = 0.5  # per enacted law, current document or order in force
    fresh_study: float = 1.0  # per study no article cites
    recent_study: float = 3.0  # per uncited study published within recent_days
    federal_action: float = 30.0  # bonus for a recent federal action
    roundup_due: float = 40.0  # bonus for a Roundup once roundup_days have passed
    rotation: float = 10.0  # per place in the type rotation, least recently used highest
    cited_share: float = 0.5  # cut by this times the share of records articles already cite
    broad: tuple[str, ...] = ("appropriates-funding", "creates-task-force")
    broad_factor: float = 0.2  # a broad provision type keeps this share of its score
    broad_changed: int = 3  # ... unless this many of its records changed this week

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> Scoring:
        base = cls()
        names = {f.name for f in fields(cls)}
        unknown = sorted(set(values or {}) - names)
        if unknown:
            raise ValueError(f"unknown scoring settings: {', '.join(unknown)}")
        out: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in (values or {}):
                continue
            v = values[f.name]
            default = getattr(base, f.name)
            out[f.name] = (
                tuple(str(x) for x in v or ()) if isinstance(default, tuple) else (type(default)(v))
            )
        return replace(base, **out)


@dataclass
class Subject:
    key: str
    type: str
    label: str
    records: list[str] = field(default_factory=list)
    studies: list[str] = field(default_factory=list)
    changed: int = 0
    window: tuple[str, str] | None = None
    group: list[str] = field(default_factory=list)  # all the records before the cap
    score: float = 0.0
    why: dict[str, float] = field(default_factory=dict)
    due: bool = True  # False for a Roundup before roundup_days have passed

    @property
    def size(self) -> int:
        return len(self.records) + len(self.studies)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "type": self.type,
            "label": self.label,
            "records": len(self.records),
            "records_in_group": len(self.group or self.records),
            "studies": len(self.studies),
            "changed": self.changed,
            "window": list(self.window) if self.window else None,
            "score": self.score,
            "why": self.why,
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
    cited: dict[str, str] = field(default_factory=dict)  # record -> newest citing article


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
        for rid, _ in a.record_notes:
            c.cited[rid] = max(c.cited.get(rid, ""), a.published)
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


# -- significance and score -----------------------------------------------------------


def settled_on(r: Record) -> str | None:
    """When the record became settled law or policy (enacted, issued as current, or
    signed and in force): its date, "" when undated, None when it is not settled."""
    if r.status != SETTLED.get(r.type):
        return None
    return next((r.dates[k] for k in SETTLED_DATES[r.type] if r.dates.get(k)), "")


def _since(day: str, cutoff: str) -> bool:
    """Whether `day` (YYYY, YYYY-MM or YYYY-MM-DD) falls on or after `cutoff`."""
    day = day[:10]
    return bool(day) and day >= cutoff[: len(day)]


def _neg(day: str) -> int:
    """A key that sorts dates (YYYY, YYYY-MM, or YYYY-MM-DD) newest first; none last."""
    digits = "".join(ch for ch in day if ch.isdigit())
    return -int((digits + "00000000")[:8])


@dataclass(frozen=True)
class Window:
    """The cutoffs for "recent" and "active" on a run date."""

    recent: str = "9999-12-31"
    active: str = "9999-12-31"

    @classmethod
    def on(cls, run_date: date, sc: Scoring) -> Window:
        return cls(
            (run_date - timedelta(days=sc.recent_days)).isoformat(),
            (run_date - timedelta(days=sc.active_days)).isoformat(),
        )


NO_WINDOW = Window()  # nothing counts as recent or active


def significance(r: Record, changed: set[str], w: Window = NO_WINDOW) -> tuple:
    """Sort key, most significant first: changed this week or settled recently; settled
    earlier; active lately; passed a chamber; other pending; closed (failed, vetoed,
    withdrawn...). The newest first within each."""
    settled = settled_on(r)
    if r.id in changed or (settled and _since(settled, w.recent)):
        rank = 0
    elif settled is not None:
        rank = 1
    elif _since(r.latest(), w.active):
        rank = 2
    elif r.status in ADVANCED:
        rank = 3
    elif r.status in CLOSED:
        rank = 5
    else:
        rank = 4
    return (rank, _neg(r.latest()), r.id)


def order_records(
    ds: Dataset, ids: Iterable[str], changed: set[str], w: Window = NO_WINDOW
) -> list[str]:
    return sorted(ids, key=lambda i: significance(ds.records[i], changed, w))


def record_score(
    records: Iterable[Record],
    changed: set[str],
    sc: Scoring,
    w: Window,
    cited: Mapping[str, str] | None = None,
) -> dict[str, float]:
    """The named parts of a group of records' score (zero parts left out). `cited` is
    the records existing articles cite."""
    recs = list(records)
    newly = [r for r in recs if (d := settled_on(r)) and _since(d, w.recent)]
    lately = [r for r in newly if _since(settled_on(r) or "", w.active)]

    def n(count: int) -> int:
        return min(count, sc.count_cap)

    parts = {
        "changed this week": sc.changed * n(sum(r.id in changed for r in recs)),
        "enacted or issued recently": sc.enacted_recent * n(len(newly)),
        "enacted or issued lately": sc.enacted_lately * n(len(lately)),
        "jurisdictions acting recently": sc.jurisdictions_recent
        * n(len({r.jurisdiction for r in newly})),
        "active lately": sc.active * n(sum(_since(r.latest(), w.active) for r in recs)),
        "settled": sc.settled * n(sum(settled_on(r) is not None for r in recs)),
    }
    if cited and recs:
        share = sum(r.id in cited for r in recs) / len(recs)
        parts["already cited by an article"] = -sc.cited_share * share * sum(parts.values())
    return {k: v for k, v in parts.items() if v}


def _scored(s: Subject, parts: Mapping[str, float]) -> Subject:
    s.why = {k: round(v, 2) for k, v in parts.items()}
    s.score = round(sum(parts.values()), 2)
    return s


# -- candidates -----------------------------------------------------------------------


def candidates(
    ds: Dataset,
    cover: Coverage,
    changed: set[str],
    *,
    run_date: date,
    limits: Limits | None = None,
    scoring: Scoring | None = None,
) -> list[Subject]:
    limits = limits or Limits()
    sc = scoring or Scoring()
    w = Window.on(run_date, sc)
    out: list[Subject] = []
    policy = list(ds.records.values())

    def top(ids: Iterable[str]) -> list[str]:
        return order_records(ds, ids, changed, w)[: limits.max_subject_records]

    def of(ids: Iterable[str]) -> list[Record]:
        return [ds.records[i] for i in ids]

    def trimmed(keep: list[str], group: list[str], noun: str) -> str:
        if len(keep) == len(group):
            return ""
        return f" (the {len(keep)} most significant of {len(group)} {noun})"

    # One state.
    by_state: dict[str, list[str]] = {}
    for r in policy:
        if r.jurisdiction != "us" and r.provisions:
            by_state.setdefault(r.jurisdiction, []).append(r.id)
    for jid, ids in by_state.items():
        key = f"jurisdiction:{jid}"
        if key in cover.keys or len(ids) < limits.min_records:
            continue
        keep = top(ids)
        name = ds.jurisdictions.get(jid, jid)
        s = Subject(
            key,
            "State brief",
            f"{name}: its recent laws and other records for K-12 and higher education"
            + trimmed(keep, ids, "records"),
            keep,
            changed=sum(i in changed for i in keep),
            group=list(ids),
        )
        out.append(_scored(s, record_score(of(keep), changed, sc, w, cover.cited)))

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
        keep = top(ids)
        parts = record_score(of(keep), changed, sc, w, cover.cited)
        n_changed = sum(i in changed for i in ids)
        if code in sc.broad and n_changed < sc.broad_changed:
            parts["broad provision type"] = -(1 - sc.broad_factor) * sum(parts.values())
        for kind in PROVISION_TYPES:
            s = Subject(
                key,
                kind,
                f"Records with a provision of type {code} "
                f"({ds.provision_types.get(code, code)})" + trimmed(keep, sorted(ids), "records"),
                keep,
                changed=sum(i in changed for i in keep),
                group=sorted(ids),
            )
            out.append(_scored(s, parts))

    # One recent federal action, with the federal records that share its provision types.
    federal = [r for r in policy if r.jurisdiction == "us" and r.provisions]
    for r in federal:
        key = f"federal:{r.id}"
        notable = settled_on(r) is not None or r.status in ADVANCED
        recent = r.id in changed or _since(r.latest(), w.active)
        covered = key in cover.keys or cover.cited.get(r.id, "") >= r.latest()[:10]
        if not notable or not recent or covered:
            continue
        codes = {p.type for p in r.provisions}
        related = [o.id for o in federal if o.id != r.id and codes & {p.type for p in o.provisions}]
        keep = [r.id, *top(related)[: limits.max_subject_records - 1]]
        if len(keep) < limits.min_federal_records:
            continue
        what = r.identifier or r.title
        s = Subject(
            key,
            "Explainer",
            f"A recent federal action: {what} ({r.issuer or 'United States'}, {r.status}), "
            "what it does, with the federal records that share its provision types",
            keep,
            changed=sum(i in changed for i in keep),
            group=[r.id, *related],
        )
        parts = {"federal action": sc.federal_action, **record_score([r], changed, sc, w)}
        out.append(_scored(s, parts))

    # Studies on one topic that no article cites.
    by_topic: dict[str, list[str]] = {}
    for st in ds.studies.values():
        for t in st.topics:
            by_topic.setdefault(t, []).append(st.id)
    for topic, ids in by_topic.items():
        key = f"research:{topic}"
        fresh = [i for i in ids if i not in cover.studies]
        if key in cover.keys or len(fresh) < limits.min_studies:
            continue
        ids = sorted(ids, key=lambda i: (i in cover.studies, _neg(ds.studies[i].published), i))
        keep = ids[: limits.max_subject_studies]
        new = [i for i in keep if i not in cover.studies]
        s = Subject(
            key,
            "Research digest",
            f"Research on {ds.topics.get(topic, topic)}"
            + trimmed(keep, ids, "studies, the newest uncited first"),
            studies=keep,
            changed=sum(i in changed for i in new),
        )
        parts = {
            "changed this week": sc.changed * min(s.changed, sc.count_cap),
            "studies no article cites": sc.fresh_study * min(len(new), sc.count_cap),
            "studies published recently": sc.recent_study
            * min(sum(_since(ds.studies[i].published, w.recent) for i in new), sc.count_cap),
        }
        out.append(_scored(s, {k: v for k, v in parts.items() if v}))

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
            keep = sorted(top(active), key=lambda i: (ds.records[i].latest(), i))
            s = Subject(
                key,
                "Roundup",
                f"What Edictai's records show from {lo} to {hi}"
                + trimmed(keep, active, "records active in the period"),
                keep,
                changed=sum(i in changed for i in keep),
                window=(lo, hi),
                group=active,
                due=due,
            )
            parts = (
                {
                    "roundup due": sc.roundup_due,
                    **record_score(of(keep), changed, sc, w, cover.cited),
                }
                if due
                else {}
            )
            out.append(_scored(s, parts))
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
    scoring: Scoring | None = None,
) -> Subject | None:
    """The subject with the highest score plus its type's rotation bonus (none for a
    Roundup that is not due). The bonus is added to the winner's `why`."""
    sc = scoring or Scoring()
    order = rotation(cover)
    pool = [
        s for s in subjects if (kind is None or s.type == kind) and (key is None or s.key == key)
    ]
    if not pool:
        return None

    def bonus(s: Subject) -> float:
        if s.type == "Roundup" and not s.due:
            return 0.0
        return sc.rotation * (len(order) - 1 - order.index(s.type))

    best = min(pool, key=lambda s: (-(s.score + bonus(s)), -s.changed, s.key, s.type))
    extra = bonus(best)
    if not extra:
        return best
    return replace(
        best, score=round(best.score + extra, 2), why={**best.why, "type rotation": extra}
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
