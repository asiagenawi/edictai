"""Flat-file exports of the database: CSV, Parquet, and a Frictionless descriptor."""

from __future__ import annotations

import csv
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .sqldoc import parse_schema

# Columns holding full YYYY-MM-DD dates. `published` and `updated` are absent
# on purpose: they may be a bare year or month.
DATE_COLUMNS = frozenset(
    {
        "date",
        "start_date",
        "end_date",
        "accessed",
        "observed",
        "signed",
        "ended",
        "expires",
        "effective",
        "last_verified",
        "status_date",
        "introduced_date",
        "enacted_date",
        "last_action_date",
    }
)


def tables(db: sqlite3.Connection) -> list[str]:
    return [
        name
        for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _primary_key(db: sqlite3.Connection, table: str) -> list[str]:
    info = db.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in sorted((r for r in info if r[5]), key=lambda r: r[5])]


def _ordered(db: sqlite3.Connection, table: str) -> sqlite3.Cursor:
    # Sorting by the key keeps exports byte-identical across rebuilds of the same data.
    order = ", ".join(_primary_key(db, table)) or "rowid"
    return db.execute(f"SELECT * FROM {table} ORDER BY {order}")


def _fresh(directory: Path) -> None:
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)


def write_csv(db_path: Path, out_dir: Path) -> None:
    _fresh(out_dir)
    db = sqlite3.connect(db_path)
    try:
        for table in tables(db):
            cur = _ordered(db, table)
            with (out_dir / f"{table}.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, lineterminator="\n")
                writer.writerow(d[0] for d in cur.description)
                writer.writerows(cur)
    finally:
        db.close()


def write_parquet(db_path: Path, out_dir: Path, *, required: bool | None = None) -> bool:
    """Write one Parquet file per table. Returns False if pyarrow is unavailable.

    `required=True` turns a missing pyarrow into an error; `False` skips Parquet.
    """
    if required is False:
        return False
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        if required:
            raise
        return False
    _fresh(out_dir)
    types = {"INTEGER": pa.int64(), "TEXT": pa.string(), "REAL": pa.float64()}
    db = sqlite3.connect(db_path)
    try:
        for table in tables(db):
            info = db.execute(f"PRAGMA table_info({table})").fetchall()
            schema = pa.schema(
                [
                    pa.field(r[1], types.get(r[2].upper(), pa.string()), nullable=not r[3])
                    for r in info
                ]
            )
            rows = _ordered(db, table).fetchall()
            columns = list(zip(*rows, strict=True)) if rows else [[] for _ in info]
            arrays = [pa.array(col, type=f.type) for col, f in zip(columns, schema, strict=True)]
            pq.write_table(
                pa.Table.from_arrays(arrays, schema=schema), out_dir / f"{table}.parquet"
            )
    finally:
        db.close()
    return True


def _field_type(column: str, sql_type: str) -> str:
    if column in DATE_COLUMNS:
        return "date"
    return {"INTEGER": "integer", "REAL": "number"}.get(sql_type.upper(), "string")


def datapackage(db_path: Path, schema_sql: Path) -> dict[str, Any]:
    docs = parse_schema(schema_sql)
    db = sqlite3.connect(db_path)
    resources = []
    try:
        for table in tables(db):
            doc = docs.get(table)
            fields = []
            for _, name, sql_type, notnull, _, pk in db.execute(f"PRAGMA table_info({table})"):
                col = doc.column(name) if doc else None
                field: dict[str, Any] = {"name": name, "type": _field_type(name, sql_type)}
                if col and col.description:
                    field["description"] = col.description
                constraints: dict[str, Any] = {}
                if notnull or pk:
                    constraints["required"] = True
                if col and col.enum:
                    constraints["enum"] = col.enum
                if constraints:
                    field["constraints"] = constraints
                fields.append(field)
            schema: dict[str, Any] = {"fields": fields, "missingValues": [""]}
            pk = _primary_key(db, table)
            if pk:
                schema["primaryKey"] = pk
            fks: dict[int, dict[str, Any]] = {}
            for fk_id, _, parent, src, dst, *_ in db.execute(f"PRAGMA foreign_key_list({table})"):
                entry = fks.setdefault(
                    fk_id, {"fields": [], "reference": {"resource": parent, "fields": []}}
                )
                entry["fields"].append(src)
                entry["reference"]["fields"].append(dst or _primary_key(db, parent)[0])
            if fks:
                schema["foreignKeys"] = [fks[k] for k in sorted(fks)]
            resource: dict[str, Any] = {
                "name": table.replace("_", "-"),
                "path": f"csv/{table}.csv",
                "profile": "tabular-data-resource",
                "format": "csv",
                "mediatype": "text/csv",
                "encoding": "utf-8",
                "schema": schema,
            }
            if doc and doc.description:
                resource["description"] = doc.description
            resources.append(resource)
    finally:
        db.close()
    # Frictionless resource names may not contain underscores, so references use the resource name.
    for resource in resources:
        for fk in resource["schema"].get("foreignKeys", []):
            fk["reference"]["resource"] = fk["reference"]["resource"].replace("_", "-")
    return {
        "profile": "tabular-data-package",
        "name": "statescope",
        "title": "StateScope: AI in Education Policy",
        "description": (
            "Legislation, agency and institutional policy documents, executive actions, and "
            "research on artificial intelligence in US K-12 and higher education."
        ),
        "homepage": "https://github.com/asiagenawi/statescope",
        "licenses": [
            {
                "name": "CC-BY-4.0",
                "title": "Creative Commons Attribution 4.0 International",
                "path": "https://creativecommons.org/licenses/by/4.0/",
            }
        ],
        "resources": resources,
    }


def write_datapackage(db_path: Path, schema_sql: Path, out_dir: Path) -> None:
    descriptor = datapackage(db_path, schema_sql)
    (out_dir / "datapackage.json").write_text(
        json.dumps(descriptor, indent=2) + "\n", encoding="utf-8"
    )
