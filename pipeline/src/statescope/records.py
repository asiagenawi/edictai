"""Load the canonical YAML records from data/."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .yamlio import load_yaml


@dataclass(frozen=True)
class Kind:
    name: str  # record_type, also the schema file stem
    directory: str
    # Number of directories between the kind directory and the file.
    depth: int
    # Template for the id implied by the path; {parts} are the intermediate directories.
    id_template: str
    path_example: str


KINDS: dict[str, Kind] = {
    k.name: k
    for k in (
        Kind("jurisdiction", "jurisdictions", 0, "{stem}", "jurisdictions/{id}.yaml"),
        Kind("organization", "organizations", 0, "{stem}", "organizations/{id}.yaml"),
        Kind(
            "legislation",
            "legislation",
            2,
            "{0}-{1}-{stem}",
            "legislation/{jurisdiction}/{session}/{bill-slug}.yaml",
        ),
        Kind("document", "documents", 1, "{0}-{stem}", "documents/{jurisdiction}/{slug}.yaml"),
        Kind(
            "executive-action",
            "executive-actions",
            1,
            "{0}-{stem}",
            "executive-actions/{jurisdiction}/{slug}.yaml",
        ),
        Kind("research", "research", 1, "{0}-{stem}", "research/{year}/{slug}.yaml"),
    )
}

# Kinds that make up the policy record set, as opposed to reference data.
POLICY_KINDS = ("legislation", "document", "executive-action")


@dataclass
class Record:
    kind: str
    path: Path  # absolute
    rel: str  # path relative to data/, with forward slashes
    data: dict[str, Any]

    @property
    def id(self) -> str | None:
        value = self.data.get("id")
        return value if isinstance(value, str) else None

    @property
    def path_parts(self) -> tuple[str, ...]:
        """Directories between the kind directory and the file."""
        return tuple(self.rel.split("/")[1:-1])

    def expected_id(self) -> str | None:
        kind = KINDS[self.kind]
        parts = self.path_parts
        if len(parts) != kind.depth:
            return None
        return kind.id_template.format(*parts, stem=self.path.stem)


@dataclass
class LoadIssue:
    rel: str
    message: str
    severity: str = "error"


@dataclass
class RecordSet:
    records: list[Record]
    issues: list[LoadIssue]

    def of(self, kind: str) -> list[Record]:
        return [r for r in self.records if r.kind == kind]

    def __iter__(self) -> Iterator[Record]:
        return iter(self.records)

    def __len__(self) -> int:
        return len(self.records)


def _rel(path: Path, data_dir: Path) -> str:
    return path.relative_to(data_dir).as_posix()


def load_records(data_dir: Path, kinds: Iterable[str] | None = None) -> RecordSet:
    """Every record under data/, in a stable order.

    A file that cannot be parsed becomes a LoadIssue rather than an exception,
    so one bad file never hides problems in the rest of the tree.
    """
    records: list[Record] = []
    issues: list[LoadIssue] = []
    for name in kinds or KINDS:
        kind = KINDS[name]
        base = data_dir / kind.directory
        if not base.is_dir():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            rel = _rel(path, data_dir)
            if path.name.startswith("."):
                continue
            if path.suffix != ".yaml":
                issues.append(LoadIssue(rel, "not a .yaml file; ignored", "warning"))
                continue
            try:
                data = load_yaml(path)
            except yaml.YAMLError as exc:
                issues.append(LoadIssue(rel, f"YAML parse error: {exc}"))
                continue
            if not isinstance(data, dict):
                issues.append(LoadIssue(rel, "file must contain a single YAML mapping"))
                continue
            records.append(Record(kind=name, path=path, rel=rel, data=data))
    return RecordSet(records, issues)
