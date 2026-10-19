"""The data the model is given for a subject: records, studies, and counts.

The model may cite only what is listed here; `Context.allowed` is what the checks
hold its notes to. Counts in `facts` are taken over every record in the subject, so
they stay right when the list itself is cut to `max_records`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .data import Article, Dataset, Record, Study
from .subjects import Subject

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
ACTIONS_LISTED = 6  # latest actions given per record (all of them in a roundup window)


def long_date(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def data_release(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.year}"


@dataclass
class Context:
    subject: Subject
    run_date: date
    payload: dict[str, Any]
    records: dict[str, Record] = field(default_factory=dict)  # listed records
    studies: dict[str, Study] = field(default_factory=dict)  # listed studies

    @property
    def as_of(self) -> str:
        return long_date(self.run_date)

    @property
    def release(self) -> str:
        return data_release(self.run_date)

    def cited(self, notes: Iterable[dict[str, Any]], cells: Iterable[str] = ()) -> dict[str, Any]:
        """The facts block and the listed records and studies the notes and table cite."""
        rids = {n["record"] for n in notes if "record" in n} | set(cells)
        sids = {n["study"] for n in notes if "study" in n}
        return {
            "facts": self.payload["facts"],
            "records": [r for r in self.payload["records"] if r["id"] in rids],
            "studies": [s for s in self.payload["studies"] if s["id"] in sids],
        }


def record_json(r: Record, changed: set[str], window: tuple[str, str] | None) -> dict[str, Any]:
    if window:
        lo, hi = window
        actions = [a for a in r.actions if lo <= a["date"][:10] <= hi]
    else:
        actions = r.actions[-ACTIONS_LISTED:]
    out: dict[str, Any] = {
        "id": r.id,
        "kind": r.type,
        "jurisdiction": r.jurisdiction_name,
        "identifier": r.identifier,
        "title": r.title,
        "classification": r.classification,
        "status": r.status,
        "dates": r.dates,
        "summary": r.summary,
        "education_levels": r.levels,
        "topics": r.topics,
        "changed_this_week": r.id in changed,
        "provisions": [
            {
                "index": p.index,
                "type": p.type,
                "description": p.description,
                "locator": p.locator,
                "quote": p.quote,
            }
            for p in r.provisions
        ],
    }
    if r.issuer:
        out["issuer"] = r.issuer
    if actions:
        out["actions"] = actions
    return out


def study_json(s: Study, changed: set[str], cited: set[str]) -> dict[str, Any]:
    return {
        "id": s.id,
        "title": s.title,
        "publisher": s.publisher,
        "published": s.published,
        "classification": s.classification,
        "peer_reviewed": s.classification == "peer-reviewed",
        "summary": s.summary,
        "methods": s.methods,
        "geography": s.geography,
        "education_levels": s.levels,
        "topics": s.topics,
        "changed_this_week": s.id in changed,
        "cited_by_an_existing_article": s.id in cited,
        "findings": [
            {"index": f.index, "finding": f.finding, "locator": f.locator, "quote": f.quote}
            for f in s.findings
        ],
    }


def facts(ds: Dataset, subject: Subject, run_date: date, listed: int) -> dict[str, Any]:
    recs = [ds.records[i] for i in subject.records]
    out: dict[str, Any] = {
        "as_of": long_date(run_date),
        "records_in_subject": len(recs),
        "records_listed": listed,
    }
    if recs:
        out["records_by_kind"] = dict(Counter(r.type for r in recs).most_common())
        leg = [r for r in recs if r.type == "legislation"]
        out["legislation_by_status"] = dict(Counter(r.status for r in leg).most_common())
        enacted = [r for r in leg if r.status == "enacted"]
        out["enacted_by_year"] = dict(
            sorted(
                Counter(
                    (r.dates.get("enacted_date") or "")[:4] or "unknown" for r in enacted
                ).items()
            )
        )
        docs = [r for r in recs if r.type != "legislation"]
        if docs:
            out["documents_and_executive_actions_by_status"] = dict(
                Counter(r.status for r in docs).most_common()
            )
        juris = Counter(r.jurisdiction_name for r in recs)
        out["jurisdictions"] = len(juris)
        out["records_by_jurisdiction"] = dict(juris.most_common())
        settled = Counter(
            r.jurisdiction_name
            for r in recs
            if r.status == "enacted" or (r.type != "legislation" and r.status == "current")
        )
        out["jurisdictions_with_enacted_law_or_current_document"] = sorted(settled)
        out["records_by_education_level"] = dict(
            Counter(lv for r in recs for lv in r.levels).most_common()
        )
    if subject.key.startswith("provision:"):
        code = subject.key.split(":", 1)[1]
        out["provision_type"] = {"code": code, "name": ds.provision_types.get(code, code)}
    if subject.studies:
        st = [ds.studies[i] for i in subject.studies]
        out["studies_in_subject"] = len(st)
        out["studies_by_classification"] = dict(Counter(s.classification for s in st).most_common())
    if subject.window:
        out["period"] = {"from": subject.window[0], "to": subject.window[1]}
    return out


def build_context(
    ds: Dataset,
    subject: Subject,
    changed: set[str],
    articles: Iterable[Article],
    *,
    run_date: date,
    max_records: int,
    cited_studies: set[str] = frozenset(),
) -> Context:
    listed = [ds.records[i] for i in subject.records[:max_records]]
    studies = [ds.studies[i] for i in subject.studies]
    payload = {
        "subject": {"type": subject.type, "label": subject.label, "key": subject.key},
        "facts": facts(ds, subject, run_date, len(listed)),
        "records": [record_json(r, changed, subject.window) for r in listed],
        "studies": [study_json(s, changed, set(cited_studies)) for s in studies],
        "existing_articles": [
            {
                "slug": a.slug,
                "type": a.type,
                "title": a.title,
                "dek": a.dek,
                "published": a.published,
            }
            for a in articles
        ],
    }
    return Context(
        subject,
        run_date,
        payload,
        {r.id: r for r in listed},
        {s.id: s for s in studies},
    )
