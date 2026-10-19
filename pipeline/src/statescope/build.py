"""Build the SQLite database and CSV/Parquet exports from data/."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from . import exports
from .paths import Paths
from .records import Record, RecordSet
from .status import derive_status
from .validate import Report, validate
from .vocab import Vocab

EXTERNAL_ID_SCHEMES = (
    "legiscan_bill_id",
    "openstates_id",
    "congress_gov",
    "federal_register_document_number",
    "doi",
    "openalex_id",
)


class BuildError(Exception):
    pass


@dataclass
class BuildResult:
    out_dir: Path
    db_path: Path
    counts: dict[str, int]
    notes: list[str] = field(default_factory=list)
    report: Report | None = None


def num_id(record_id: str) -> int:
    """48-bit integer alias for a record id: stable, and exact in JavaScript numbers."""
    return int.from_bytes(hashlib.sha1(record_id.encode()).digest()[:6], "big")


def _levels(values: Any) -> tuple[int, int]:
    values = values or []
    return int("k12" in values), int("postsecondary" in values)


def _source_row(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": s["url"],
        "title": s.get("title"),
        "publisher": s.get("publisher"),
        "kind": s["kind"],
        "accessed": s["accessed"],
        "archived_url": s.get("archived_url"),
    }


class _Loader:
    def __init__(self, db: sqlite3.Connection, records: list[Record], today: date):
        self.db = db
        self.records = records
        self.today = today
        self.ids = {r.id for r in records if r.id}
        self.by_kind = {
            k: {r.id for r in records if r.kind == k and r.id}
            for k in ("jurisdiction", "organization", "document")
        }
        self.notes: list[str] = []
        self.session_ends: dict[tuple[str, str], str | None] = {}

    def insert(self, table: str, rows: Iterable[dict[str, Any]]) -> None:
        rows = list(rows)
        if not rows:
            return
        cols = list(rows[0])
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        self.db.executemany(sql, [tuple(r[c] for c in cols) for r in rows])

    def ref(self, kind: str | None, value: Any, record: Record, field_name: str) -> Any:
        """A cross-reference, or None when its target is not in this build."""
        if value is None:
            return None
        known = self.ids if kind is None else self.by_kind[kind]
        if value in known:
            return value
        self.notes.append(f"{record.rel}: {field_name} {value!r} not found; stored as NULL")
        return None

    def vocab(self, vocab: Vocab) -> None:
        self.insert(
            "topics",
            (
                {
                    "code": t["code"],
                    "name": t["name"],
                    "description": t.get("description"),
                    "position": i,
                    "k12": _levels(t.get("education_levels"))[0],
                    "postsecondary": _levels(t.get("education_levels"))[1],
                }
                for i, t in enumerate(vocab.topics, 1)
            ),
        )
        self.insert(
            "provision_types",
            (
                {
                    "code": p["code"],
                    "name": p["name"],
                    "topic": p["topic"],
                    "position": i,
                    "k12": _levels(p.get("education_levels"))[0],
                    "postsecondary": _levels(p.get("education_levels"))[1],
                }
                for i, p in enumerate(vocab.provision_types, 1)
            ),
        )

    def registry(self) -> None:
        self.insert(
            "records",
            (
                {"id": r.id, "record_type": r.kind, "path": f"data/{r.rel}", "num_id": num_id(r.id)}
                for r in self.records
            ),
        )

    def jurisdiction(self, r: Record) -> None:
        d = r.data
        self.insert(
            "jurisdictions",
            [
                {
                    "id": r.id,
                    "ocd_id": d["ocd_id"],
                    "name": d["name"],
                    "abbreviation": d["abbreviation"],
                    "classification": d["classification"],
                    "fips": d.get("fips"),
                    "region": d.get("region"),
                    "legislature_url": d.get("legislature_url"),
                }
            ],
        )
        sessions = d.get("sessions") or []
        for s in sessions:
            self.session_ends[(r.id, str(s["identifier"]))] = s.get("end_date")
        self.insert(
            "sessions",
            (
                {
                    "jurisdiction": r.id,
                    "identifier": str(s["identifier"]),
                    "name": s["name"],
                    "classification": s.get("classification"),
                    "start_date": s["start_date"],
                    "end_date": s.get("end_date"),
                    "legiscan_session_id": s.get("legiscan_session_id"),
                }
                for s in sessions
            ),
        )
        self.insert(
            "session_sources",
            (
                {
                    "jurisdiction": r.id,
                    "session": str(s["identifier"]),
                    "seq": i,
                    **_source_row(src),
                }
                for s in sessions
                for i, src in enumerate(s.get("sources") or [])
            ),
        )

    def organization(self, r: Record) -> None:
        d = r.data
        self.insert(
            "organizations",
            [
                {
                    "id": r.id,
                    "name": d["name"],
                    "abbreviation": d.get("abbreviation"),
                    "classification": d["classification"],
                    "jurisdiction": self.ref(
                        "jurisdiction", d.get("jurisdiction"), r, "jurisdiction"
                    ),
                    "url": d["url"],
                }
            ],
        )
        self.insert(
            "organization_watch_urls",
            (
                {"organization_id": r.id, "seq": i, "url": u}
                for i, u in enumerate(d.get("watch_urls") or [])
            ),
        )
        self.levels(r)

    @staticmethod
    def _verification(d: dict[str, Any]) -> dict[str, Any]:
        v = d.get("verification") or {}
        return {
            "verification_status": v.get("status"),
            "last_verified": v.get("last_verified"),
            "verification_method": v.get("method"),
            "verification_notes": v.get("notes"),
        }

    def legislation(self, r: Record) -> None:
        d = r.data
        session = str(d["session"])
        derived = derive_status(
            d["actions"],
            jurisdiction=d["jurisdiction"],
            classification=d["classification"],
            session_end=self.session_ends.get((d["jurisdiction"], session)),
            today=self.today,
        )
        self.insert(
            "legislation",
            [
                {
                    "id": r.id,
                    "jurisdiction": d["jurisdiction"],
                    "session": session,
                    "identifier": d["identifier"],
                    "title": d["title"],
                    "classification": d["classification"],
                    "summary": d["summary"],
                    "abstract": d.get("abstract"),
                    "session_law": d.get("session_law"),
                    "status": derived.status,
                    "status_date": derived.status_date,
                    "introduced_date": derived.introduced,
                    "enacted_date": derived.enacted,
                    "last_action_date": derived.last_action,
                    **self._verification(d),
                }
            ],
        )
        actions = d["actions"]
        self.insert(
            "legislation_actions",
            (
                {
                    "legislation_id": r.id,
                    "seq": i,
                    "date": a["date"],
                    "actor": a.get("actor"),
                    "description": a["description"],
                }
                for i, a in enumerate(actions)
            ),
        )
        self.insert(
            "legislation_action_classifications",
            (
                {"legislation_id": r.id, "seq": i, "classification": c}
                for i, a in enumerate(actions)
                for c in dict.fromkeys(a["classification"])
            ),
        )
        self.insert(
            "legislation_versions",
            (
                {
                    "legislation_id": r.id,
                    "seq": i,
                    "note": v["note"],
                    "date": v.get("date"),
                    "url": v["url"],
                    "archived_url": v.get("archived_url"),
                    "media_type": v.get("media_type"),
                }
                for i, v in enumerate(d.get("versions") or [])
            ),
        )
        self.insert(
            "legislation_sponsors",
            (
                {
                    "legislation_id": r.id,
                    "seq": i,
                    "name": s["name"],
                    "classification": s.get("classification") or "person",
                    "primary_sponsor": int(bool(s["primary"])),
                    "party": s.get("party"),
                    "chamber": s.get("chamber"),
                    "openstates_person_id": s.get("openstates_person_id"),
                }
                for i, s in enumerate(d.get("sponsors") or [])
            ),
        )
        self.common(r)

    def document(self, r: Record) -> None:
        d = r.data
        self.insert(
            "documents",
            [
                {
                    "id": r.id,
                    "jurisdiction": d["jurisdiction"],
                    "issuer": self.ref("organization", d.get("issuer"), r, "issuer"),
                    "title": d["title"],
                    "classification": d["classification"],
                    "status": d["status"],
                    "summary": d["summary"],
                    "published": d.get("published"),
                    "updated": d.get("updated"),
                    "version": d.get("version"),
                    "effective": d.get("effective"),
                    "undated": int(bool(d.get("undated"))),
                    "superseded_by": self.ref(
                        "document", d.get("superseded_by"), r, "superseded_by"
                    ),
                    "authorized_by": self.ref(None, d.get("authorized_by"), r, "authorized_by"),
                    **self._verification(d),
                }
            ],
        )
        self.insert(
            "document_revisions",
            (
                {
                    "document_id": r.id,
                    "seq": i,
                    "observed": v["observed"],
                    "url": v["url"],
                    "archived_url": v.get("archived_url"),
                    "sha256": v.get("sha256"),
                    "note": v.get("note"),
                }
                for i, v in enumerate(d.get("revisions") or [])
            ),
        )
        self.common(r)

    def executive_action(self, r: Record) -> None:
        d = r.data
        self.insert(
            "executive_actions",
            [
                {
                    "id": r.id,
                    "jurisdiction": d["jurisdiction"],
                    "issuer": self.ref("organization", d.get("issuer"), r, "issuer"),
                    "number": d.get("number"),
                    "title": d["title"],
                    "classification": d["classification"],
                    "status": d["status"],
                    "signed": d["signed"],
                    "ended": d.get("ended"),
                    "expires": d.get("expires"),
                    "summary": d["summary"],
                    **self._verification(d),
                }
            ],
        )
        self.common(r)

    def research(self, r: Record) -> None:
        d = r.data
        self.insert(
            "research",
            [
                {
                    "id": r.id,
                    "title": d["title"],
                    "publisher": d["publisher"],
                    "published": d["published"],
                    "classification": d["classification"],
                    "summary": d["summary"],
                    "methods": d.get("methods"),
                    **self._verification(d),
                }
            ],
        )
        self.insert(
            "research_authors",
            (
                {"research_id": r.id, "seq": i, "name": a}
                for i, a in enumerate(d.get("authors") or [])
            ),
        )
        geography = [
            j
            for j in dict.fromkeys(d.get("geography") or [])
            if self.ref("jurisdiction", j, r, "geography")
        ]
        self.insert(
            "research_geography",
            ({"research_id": r.id, "jurisdiction": j} for j in geography),
        )
        self.insert(
            "research_findings",
            (
                {
                    "research_id": r.id,
                    "seq": i,
                    "finding": f["finding"],
                    "source_url": f["citation"]["source_url"],
                    "locator": f["citation"].get("locator"),
                    "quote": f["citation"].get("quote"),
                }
                for i, f in enumerate(d.get("key_findings") or [])
            ),
        )
        self.common(r)

    def levels(self, r: Record) -> None:
        self.insert(
            "record_education_levels",
            (
                {"record_id": r.id, "record_type": r.kind, "education_level": lv}
                for lv in dict.fromkeys(r.data.get("education_levels") or [])
            ),
        )

    def common(self, r: Record) -> None:
        """Rows shared by every policy and research record."""
        d = r.data
        self.levels(r)
        self.insert(
            "record_topics",
            (
                {"record_id": r.id, "record_type": r.kind, "topic": t}
                for t in dict.fromkeys(d.get("topics") or [])
            ),
        )
        self.insert(
            "sources",
            (
                {
                    "record_id": r.id,
                    "record_type": r.kind,
                    "seq": i,
                    **_source_row(s),
                }
                for i, s in enumerate(d.get("sources") or [])
            ),
        )
        related = [
            x
            for x in dict.fromkeys(d.get("related") or [])
            if x != r.id and self.ref(None, x, r, "related")
        ]
        self.insert("related_records", ({"record_id": r.id, "related_id": x} for x in related))
        external = d.get("external_ids") or {}
        self.insert(
            "external_ids",
            (
                {"record_id": r.id, "record_type": r.kind, "scheme": k, "value": str(external[k])}
                for k in EXTERNAL_ID_SCHEMES
                if k in external
            ),
        )
        record_levels = d.get("education_levels") or []
        for i, p in enumerate(d.get("provisions") or []):
            cite = p["citation"]
            cur = self.db.execute(
                "INSERT INTO provisions (record_id, record_type, seq, type, description, source_url, "
                "locator, quote) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r.id,
                    r.kind,
                    i,
                    p["type"],
                    p["description"],
                    cite["source_url"],
                    cite.get("locator"),
                    cite.get("quote"),
                ),
            )
            own = p.get("education_levels")
            self.insert(
                "provision_education_levels",
                (
                    {
                        "provision_id": cur.lastrowid,
                        "education_level": lv,
                        "inherited": int(own is None),
                    }
                    for lv in dict.fromkeys(own if own is not None else record_levels)
                ),
            )


_LOADERS: dict[str, Callable[[_Loader, Record], None]] = {
    "jurisdiction": _Loader.jurisdiction,
    "organization": _Loader.organization,
    "legislation": _Loader.legislation,
    "document": _Loader.document,
    "executive-action": _Loader.executive_action,
    "research": _Loader.research,
}


def build_database(
    db_path: Path,
    schema_sql: Path,
    vocab: Vocab,
    records: list[Record],
    *,
    today: date | None = None,
) -> list[str]:
    """Write the database to db_path. Returns notes about references that were dropped."""
    tmp = db_path.with_suffix(".db.tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    try:
        db.executescript(schema_sql.read_text(encoding="utf-8"))
        # Load with enforcement off so insert order does not matter, then check
        # every constraint at once and report all violations together.
        db.execute("PRAGMA foreign_keys = OFF")
        loader = _Loader(db, records, today or date.today())
        loader.vocab(vocab)
        loader.registry()
        for kind, load in _LOADERS.items():
            for r in records:
                if r.kind == kind:
                    load(loader, r)
        db.commit()
        violations = db.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            detail = "; ".join(
                f"{t} row {rowid} -> {parent}" for t, rowid, parent, _ in violations[:20]
            )
            raise BuildError(f"{len(violations)} foreign key violations: {detail}")
        db.execute("PRAGMA optimize")
        db.close()
    except Exception:
        db.close()
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(db_path)
    return loader.notes


def build(
    paths: Paths,
    out_dir: Path | None = None,
    *,
    skip_invalid: bool = False,
    parquet: bool | None = None,
    today: date | None = None,
    log: Callable[[str], None] = print,
) -> BuildResult:
    out_dir = out_dir or paths.root / "build"
    report, records = validate(paths, today=today)
    selected = _select(records, report, skip_invalid, log)

    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "statescope.db"
    vocab = Vocab.load(paths.vocab)
    try:
        notes = build_database(db_path, paths.db_schema, vocab, selected, today=today)
    except sqlite3.IntegrityError as exc:
        raise BuildError(f"database constraint failed: {exc}") from exc
    if notes:
        # Each of these is also a validation warning, so one line is enough here.
        log(f"note: {len(notes)} references to records not in this build were stored as NULL")

    db = sqlite3.connect(db_path)
    try:
        counts = {
            t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for (t,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        }
    finally:
        db.close()

    exports.write_csv(db_path, out_dir / "csv")
    exports.write_datapackage(db_path, paths.db_schema, out_dir)
    wrote_parquet = exports.write_parquet(db_path, out_dir / "parquet", required=parquet)
    if not wrote_parquet:
        log("note: pyarrow not installed; skipped Parquet (install the 'parquet' extra)")

    return BuildResult(out_dir, db_path, counts, notes, report)


def _select(
    records: RecordSet, report: Report, skip_invalid: bool, log: Callable[[str], None]
) -> list[Record]:
    if report.ok:
        return list(records)
    if not skip_invalid:
        raise BuildError(
            f"validation failed ({len(report.errors)} errors); run `statescope validate` "
            "or pass --skip-invalid to build from the valid records only"
        )
    kept = [r for r in records if f"data/{r.rel}" not in report.invalid_files]
    # Optional references to a skipped record are stored as NULL, but a record
    # cannot exist without its jurisdiction, so those go too.
    jurisdictions = {r.id for r in kept if r.kind == "jurisdiction"}
    kept = [
        r
        for r in kept
        if r.kind == "jurisdiction"
        or r.data.get("jurisdiction") is None
        or r.data.get("jurisdiction") in jurisdictions
    ]
    log(f"note: skipped {len(records) - len(kept)} invalid records")
    return kept
