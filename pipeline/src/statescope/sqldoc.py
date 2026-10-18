"""Read table and column descriptions out of db/schema.sql.

The comments in schema.sql are the single place the database is described;
the data dictionary and datapackage.json are generated from them so the three
cannot disagree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_CREATE = re.compile(r"^CREATE (TABLE|VIEW) (\w+)")
_COLUMN = re.compile(r"^\s+(\w+) (INTEGER|TEXT|REAL|BLOB)\b(.*?)(?:,)?\s*(?:--\s*(.*))?$")
_ENUM = re.compile(r"CHECK \(\w+ IN \(([^)]*)\)\)")


@dataclass
class Column:
    name: str
    type: str
    description: str = ""
    enum: list[str] | None = None


@dataclass
class Table:
    name: str
    kind: str  # 'table' or 'view'
    description: str = ""
    columns: list[Column] = field(default_factory=list)

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


def parse_schema(path: Path) -> dict[str, Table]:
    tables: dict[str, Table] = {}
    pending: list[str] = []
    current: Table | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        match = _CREATE.match(line)
        if match:
            kind, name = match.groups()
            current = Table(name, kind.lower(), " ".join(pending))
            tables[name] = current
            pending = []
            continue
        if stripped.startswith("--"):
            text = stripped.lstrip("-").strip()
            # Banner rules are layout, not description.
            if text and not set(text) <= {"-"}:
                pending.append(text)
            else:
                pending = []
            continue
        if not stripped:
            pending = []
        if current and current.kind == "table":
            col = _COLUMN.match(line)
            if col:
                name, typ, rest, comment = col.groups()
                enum = _ENUM.search(rest)
                values = re.findall(r"'([^']*)'", enum.group(1)) if enum else None
                current.columns.append(Column(name, typ, (comment or "").strip(), values))
        if stripped.endswith(";"):
            current = None
    return tables
