"""Generate docs/data/dictionary.md from the schemas, vocabularies, and db/schema.sql."""

from __future__ import annotations

from typing import Any

from .paths import Paths
from .records import KINDS
from .schemas import BASE_KEY, Schemas
from .sqldoc import parse_schema
from .vocab import Vocab

ORDER = ("legislation", "document", "executive-action", "research", "organization", "jurisdiction")

_FORMATS = {
    r"^\d{4}-\d{2}-\d{2}$": "date, `YYYY-MM-DD`",
    r"^\d{4}(-\d{2}(-\d{2})?)?$": "date, `YYYY`, `YYYY-MM`, or `YYYY-MM-DD`",
}


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _anchor(name: str) -> str:
    return name.lower().replace(" ", "-")


class _Writer:
    def __init__(self, schemas: Schemas, vocab: Vocab):
        self.schemas = schemas
        self.vocab = vocab
        self.lines: list[str] = []

    def out(self, *lines: str) -> None:
        self.lines.extend(lines)

    def resolve(self, schema: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
        return self.schemas.resolve(schema, base)

    def type_of(self, s: dict[str, Any], base: dict[str, Any]) -> str:
        t = s.get("type", "")
        if t == "array":
            item = self.resolve(s.get("items", {}), base)
            inner = self.type_of(item, base)
            return f"list of {inner}s" if inner else "list"
        if t == "string" and s.get("pattern") in _FORMATS:
            return _FORMATS[s["pattern"]]
        if t == "string" and s.get("format") == "uri":
            return "URL"
        return t

    def allowed(self, s: dict[str, Any], base: dict[str, Any]) -> str:
        if s.get("type") == "array":
            return self.allowed(self.resolve(s.get("items", {}), base), base)
        parts = []
        if "x-vocab" in s:
            name = s["x-vocab"]
            parts.append(f"code from [{name}](#{_anchor(name)})")
        if "enum" in s:
            parts.append(", ".join(f"`{v}`" for v in s["enum"]))
        if "x-ref" in s:
            target = s["x-ref"]
            parts.append("id of any record" if target == "any" else f"id of a {target} record")
            if target == "session":
                parts[-1] = "a session identifier in the jurisdiction's file"
        if "pattern" in s and s["pattern"] not in _FORMATS and s.get("format") != "uri":
            parts.append(f"pattern `{s['pattern']}`")
        if s.get("minItems"):
            parts.append(f"at least {s['minItems']}")
        if "maxLength" in s:
            parts.append(f"at most {s['maxLength']} characters")
        return "; ".join(parts)

    def rows(self, schema: dict[str, Any], base: dict[str, Any], prefix: str = "") -> None:
        required = set(schema.get("required", []))
        for name, raw in schema.get("properties", {}).items():
            s = self.resolve(raw, base)
            sbase = s.get(BASE_KEY, base)
            path = f"{prefix}{name}"
            self.out(
                f"| `{path}` | {_cell(self.type_of(s, sbase))} | {'yes' if name in required else ''} "
                f"| {_cell(s.get('description', ''))} | {_cell(self.allowed(s, sbase))} |"
            )
            if s.get("type") == "object" and "properties" in s:
                self.rows(s, sbase, f"{path}.")
            elif s.get("type") == "array":
                item = self.resolve(s.get("items", {}), sbase)
                if item.get("type") == "object":
                    self.rows(item, item.get(BASE_KEY, sbase), f"{path}[].")

    def record(self, kind: str) -> None:
        schema = self.schemas.by_kind[kind]
        self.out(f"### {schema['title']}", "", schema.get("description", ""), "")
        self.out(f"File: `data/{KINDS[kind].path_example}`", "")
        self.out(
            "| Field | Type | Required | Description | Allowed values |", "|---|---|---|---|---|"
        )
        self.rows(schema, schema)
        self.out("")

    def vocabularies(self) -> None:
        self.out("## Vocabularies", "")
        self.out(
            "Fields marked with a vocabulary take one of its codes. The validator rejects any "
            "other value. Codes are permanent; labels may be reworded.",
            "",
        )
        self.out("### topics", "", "From `data/vocab/topics.yaml`.", "")
        self.out("| Code | Name | Description | Education levels |", "|---|---|---|---|")
        for t in self.vocab.topics:
            levels = ", ".join(t.get("education_levels") or [])
            self.out(
                f"| `{t['code']}` | {_cell(t['name'])} | {_cell(t.get('description', ''))} | {levels} |"
            )
        self.out("", "### provision_types", "", "From `data/vocab/provision-types.yaml`.", "")
        self.out("| Code | Name | Topic | Education levels |", "|---|---|---|---|")
        for p in self.vocab.provision_types:
            levels = ", ".join(p.get("education_levels") or [])
            self.out(f"| `{p['code']}` | {_cell(p['name'])} | `{p.get('topic', '')}` | {levels} |")
        self.out("")
        for name, value in self.vocab.enums.items():
            self.out(f"### {name}", "", "From `data/vocab/enums.yaml`.", "")
            if isinstance(value, dict):
                self.out("| Code | Meaning |", "|---|---|")
                for code, label in value.items():
                    self.out(f"| `{code}` | {_cell(label)} |")
            else:
                self.out("| Code |", "|---|")
                for code in value:
                    self.out(f"| `{code}` |")
            self.out("")

    def database(self, paths: Paths) -> None:
        tables = parse_schema(paths.db_schema)
        self.out("## Database", "")
        self.out(
            "`statescope build` writes these tables to `build/statescope.db`, and one CSV and "
            "Parquet file per table. Definitions are in `db/schema.sql`.",
            "",
        )
        for table in tables.values():
            if table.kind != "table":
                continue
            self.out(f"### `{table.name}`", "", table.description, "")
            self.out("| Column | Type | Description | Allowed values |", "|---|---|---|---|")
            for c in table.columns:
                allowed = ", ".join(f"`{v}`" for v in c.enum) if c.enum else ""
                self.out(f"| `{c.name}` | {c.type} | {_cell(c.description)} | {allowed} |")
            self.out("")
        views = [t for t in tables.values() if t.kind == "view"]
        if views:
            self.out("### Views", "", "| View | Description |", "|---|---|")
            for v in views:
                self.out(f"| `{v.name}` | {_cell(v.description)} |")
            self.out("")


def render(paths: Paths) -> str:
    schemas = Schemas.load(paths.schema)
    vocab = Vocab.load(paths.vocab)
    w = _Writer(schemas, vocab)
    w.out(
        "# Data dictionary",
        "",
        "<!-- Generated by `statescope docs` from data/schema/, data/vocab/, and db/schema.sql. "
        "Edit those files and regenerate; changes made here are overwritten. -->",
        "",
        "Every field of every record type, the controlled vocabularies they draw on, and the "
        "tables of the built database.",
        "",
        "## Records",
        "",
        "Field paths use `a.b` for a field of an object and `a[].b` for a field of each item in a list.",
        "",
    )
    for kind in ORDER:
        if kind in schemas.by_kind:
            w.record(kind)
    w.vocabularies()
    w.database(paths)
    text = "\n".join(w.lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.rstrip() + "\n"


def write(paths: Paths, *, check: bool = False) -> bool:
    """Write the dictionary, or with check=True report whether it is up to date."""
    text = render(paths)
    target = paths.dictionary
    if check:
        return target.exists() and target.read_text(encoding="utf-8") == text
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return True
