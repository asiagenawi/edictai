"""The records, research, and existing articles an article is written from.

Records and studies are read from the built database (build/edictai.db), the same
build the site reads, so a note that resolves here resolves in the site build. A
provision or finding is addressed by its position in the record's list, as the site
addresses it.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..yamlio import load_yaml

POLICY_TYPES = ("legislation", "document", "executive-action")


@dataclass
class Provision:
    index: int
    type: str
    description: str
    locator: str | None
    quote: str | None


@dataclass
class Record:
    id: str
    type: str  # legislation, document, executive-action
    jurisdiction: str
    jurisdiction_name: str
    identifier: str | None
    title: str
    classification: str
    status: str
    summary: str
    path: str
    issuer: str | None = None
    dates: dict[str, str] = field(default_factory=dict)
    topics: list[str] = field(default_factory=list)
    levels: list[str] = field(default_factory=list)
    provisions: list[Provision] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)  # {date, description}

    def activity(self) -> list[tuple[str, str]]:
        """(date, what happened) for every dated event the record lists."""
        out = [(a["date"], a["description"]) for a in self.actions if a.get("date")]
        labels = {"published": "Published", "updated": "Updated", "signed": "Signed"}
        out += [(self.dates[k], v) for k, v in labels.items() if self.dates.get(k)]
        return out

    def latest(self) -> str:
        return max([d for d, _ in self.activity()] + [""])


@dataclass
class Finding:
    index: int
    finding: str
    locator: str | None
    quote: str | None


@dataclass
class Study:
    id: str
    title: str
    publisher: str
    published: str
    classification: str
    summary: str
    methods: str | None
    path: str
    verification: str
    topics: list[str] = field(default_factory=list)
    levels: list[str] = field(default_factory=list)
    geography: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


@dataclass
class Dataset:
    records: dict[str, Record]
    studies: dict[str, Study]
    jurisdictions: dict[str, str]  # id -> name
    topics: dict[str, str]  # code -> name
    provision_types: dict[str, str]  # code -> name
    paths: dict[str, str]  # repository-relative file path -> record id


def _rows(db: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    return list(db.execute(sql))


def load_dataset(db_path: Path) -> Dataset:
    if not db_path.exists():
        raise FileNotFoundError(f"{db_path} not found; run `edictai build` first")
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        juris = {r["id"]: r["name"] for r in _rows(db, "SELECT id, name FROM jurisdictions")}
        orgs = {r["id"]: r["name"] for r in _rows(db, "SELECT id, name FROM organizations")}
        topics = {r["code"]: r["name"] for r in _rows(db, "SELECT code, name FROM topics")}
        ptypes = {r["code"]: r["name"] for r in _rows(db, "SELECT code, name FROM provision_types")}
        paths = {r["path"]: r["id"] for r in _rows(db, "SELECT id, path FROM records")}
        by_id_path = {v: k for k, v in paths.items()}
        tps: dict[str, list[str]] = defaultdict(list)
        for r in _rows(db, "SELECT record_id, topic FROM record_topics"):
            tps[r["record_id"]].append(r["topic"])
        lvls: dict[str, list[str]] = defaultdict(list)
        for r in _rows(db, "SELECT record_id, education_level FROM record_education_levels"):
            lvls[r["record_id"]].append(r["education_level"])
        provs: dict[str, list[Provision]] = defaultdict(list)
        for r in _rows(db, "SELECT * FROM provisions ORDER BY record_id, seq"):
            lst = provs[r["record_id"]]
            lst.append(Provision(len(lst), r["type"], r["description"], r["locator"], r["quote"]))
        acts: dict[str, list[dict[str, str]]] = defaultdict(list)
        for r in _rows(db, "SELECT * FROM legislation_actions ORDER BY legislation_id, seq"):
            acts[r["legislation_id"]].append(
                {"date": r["date"] or "", "description": r["description"]}
            )

        records: dict[str, Record] = {}

        def add(kind: str, r: sqlite3.Row, identifier: str | None, dates: dict[str, Any]):
            issuer = r["issuer"] if "issuer" in r.keys() else None  # noqa: SIM118 (Row: values)
            records[r["id"]] = Record(
                id=r["id"],
                type=kind,
                jurisdiction=r["jurisdiction"],
                jurisdiction_name="United States"
                if r["jurisdiction"] == "us"
                else juris.get(r["jurisdiction"], r["jurisdiction"]),
                identifier=identifier,
                title=r["title"],
                classification=r["classification"],
                status=r["status"],
                summary=r["summary"],
                path=by_id_path.get(r["id"], ""),
                issuer=orgs.get(issuer) if issuer else None,
                dates={k: str(v) for k, v in dates.items() if v},
                topics=tps.get(r["id"], []),
                levels=lvls.get(r["id"], []),
                provisions=provs.get(r["id"], []),
                actions=acts.get(r["id"], []),
            )

        for r in _rows(db, "SELECT * FROM legislation"):
            add(
                "legislation",
                r,
                r["identifier"],
                {
                    k: r[k]
                    for k in ("introduced_date", "status_date", "enacted_date", "last_action_date")
                },
            )
        for r in _rows(db, "SELECT * FROM documents"):
            add("document", r, None, {k: r[k] for k in ("published", "updated", "effective")})
        for r in _rows(db, "SELECT * FROM executive_actions"):
            add("executive-action", r, r["number"], {k: r[k] for k in ("signed", "ended")})

        geo: dict[str, list[str]] = defaultdict(list)
        for r in _rows(db, "SELECT research_id, jurisdiction FROM research_geography"):
            geo[r["research_id"]].append(r["jurisdiction"])
        finds: dict[str, list[Finding]] = defaultdict(list)
        for r in _rows(db, "SELECT * FROM research_findings ORDER BY research_id, seq"):
            lst = finds[r["research_id"]]
            lst.append(Finding(len(lst), r["finding"], r["locator"], r["quote"]))
        studies = {
            r["id"]: Study(
                id=r["id"],
                title=r["title"],
                publisher=r["publisher"],
                published=r["published"],
                classification=r["classification"],
                summary=r["summary"],
                methods=r["methods"],
                path=by_id_path.get(r["id"], ""),
                verification=r["verification_status"],
                topics=tps.get(r["id"], []),
                levels=lvls.get(r["id"], []),
                geography=geo.get(r["id"], []),
                findings=finds.get(r["id"], []),
            )
            for r in _rows(db, "SELECT * FROM research")
        }
    finally:
        db.close()
    return Dataset(records, studies, juris, topics, ptypes, paths)


# -- existing articles ----------------------------------------------------------------


@dataclass
class Article:
    slug: str
    path: Path
    title: str
    dek: str
    type: str
    published: str
    record_notes: list[tuple[str, int]]  # (record id, provision index)
    study_notes: list[tuple[str, int]]  # (study id, finding index)
    drawing: str | None = None  # the `drawing` field, when the file sets one


def load_articles(content_dir: Path) -> list[Article]:
    out = []
    for path in sorted(content_dir.glob("**/*.y*ml")):
        data = load_yaml(path) or {}
        rec, stu = [], []
        for n in data.get("notes") or []:
            if not isinstance(n, Mapping):
                continue
            if "record" in n:
                rec.append((str(n["record"]), int(n.get("provision", 0) or 0)))
            elif "study" in n:
                stu.append((str(n["study"]), int(n.get("finding", 0) or 0)))
        out.append(
            Article(
                slug=path.stem,
                path=path,
                title=str(data.get("title") or ""),
                dek=str(data.get("dek") or ""),
                type=str(data.get("type") or ""),
                published=str(data.get("published") or ""),
                record_notes=rec,
                study_notes=stu,
                drawing=str(data["drawing"]) if data.get("drawing") else None,
            )
        )
    return out


# -- what changed ---------------------------------------------------------------------


def changed_ids(
    ds: Dataset,
    changed_paths: Iterable[str],
    *,
    since: date,
    until: date,
) -> set[str]:
    """Records and studies whose files changed in this run, and records with an action,
    publication, update, or signature dated in [since, until]."""
    out = {ds.paths[p] for p in changed_paths if p in ds.paths}
    lo, hi = since.isoformat(), until.isoformat()
    for r in ds.records.values():
        if any(lo <= d[:10] <= hi for d, _ in r.activity() if len(d) >= 10):
            out.add(r.id)
    return {i for i in out if i in ds.records or i in ds.studies}
