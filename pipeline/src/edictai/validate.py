"""Validate the records in data/ against the schemas, vocabularies, and each other."""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from jsonschema import Draft202012Validator, ValidationError, validators

from .paths import Paths
from .records import KINDS, Record, RecordSet, load_records
from .schemas import Schemas
from .vocab import Vocab

ERROR = "error"
WARNING = "warning"

_DATE_PATTERNS = {
    r"^\d{4}-\d{2}-\d{2}$": "%Y-%m-%d",
    r"^\d{4}(-\d{2}(-\d{2})?)?$": None,  # partial date: checked by length below
}

# Fields that record when something was observed, so they cannot be in the future.
_OBSERVED_FIELDS = (("sources", "accessed"), ("verification", "last_verified"))


@dataclass(frozen=True)
class Issue:
    severity: str
    file: str  # relative to the repository root
    pointer: str  # JSON pointer into the record, "" for the whole file
    message: str
    rule: str

    def format(self) -> str:
        where = f"{self.file}#{self.pointer}" if self.pointer else self.file
        return f"{where}: {self.severity} [{self.rule}] {self.message}"

    def github(self) -> str:
        level = "error" if self.severity == ERROR else "warning"
        detail = f"{self.pointer}: {self.message}" if self.pointer else self.message
        return f"::{level} file={self.file},title={self.rule}::{detail}"


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    records_checked: int = 0
    # Records with at least one error, by repository-relative path.
    invalid_files: set[str] = field(default_factory=set)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        files = len({i.file for i in self.issues})
        return (
            f"{self.records_checked} records checked: {len(self.errors)} errors, "
            f"{len(self.warnings)} warnings in {files} files"
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "records_checked": self.records_checked,
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "issues": [asdict(i) for i in self.issues],
            },
            indent=2,
        )


def pointer(path: Any) -> str:
    parts = [str(p).replace("~", "~0").replace("/", "~1") for p in path]
    return "/" + "/".join(parts) if parts else ""


def _suggest(value: str, options: set[str]) -> str:
    close = difflib.get_close_matches(value, sorted(options), n=1)
    return f"; did you mean {close[0]!r}?" if close else ""


@dataclass
class Index:
    """Ids of every loaded record, for resolving x-ref cross-references."""

    by_kind: dict[str, set[str]]
    all_ids: set[str]
    sessions: dict[str, set[str]]  # jurisdiction id -> session identifiers
    session_ends: dict[tuple[str, str], str | None]

    @classmethod
    def build(cls, records: RecordSet) -> Index:
        by_kind: dict[str, set[str]] = {k: set() for k in KINDS}
        sessions: dict[str, set[str]] = {}
        session_ends: dict[tuple[str, str], str | None] = {}
        for r in records:
            if r.id:
                by_kind[r.kind].add(r.id)
            if r.kind == "jurisdiction" and r.id:
                for s in r.data.get("sessions") or []:
                    if isinstance(s, dict) and "identifier" in s:
                        ident = str(s["identifier"])
                        sessions.setdefault(r.id, set()).add(ident)
                        session_ends[(r.id, ident)] = s.get("end_date")
        all_ids = set().union(*by_kind.values())
        return cls(by_kind, all_ids, sessions, session_ends)


class _Checker:
    """Runs every rule over a loaded record set."""

    def __init__(self, paths: Paths, records: RecordSet, *, strict: bool, today: date):
        self.paths = paths
        self.records = records
        self.strict = strict
        self.today = today
        self.vocab = Vocab.load(paths.vocab)
        self.schemas = Schemas.load(paths.schema)
        self.index = Index.build(records)
        self.report = Report(records_checked=len(records))
        self._current: Record | None = None
        self._validators = {
            kind: self._validator_class()(schema, registry=self.schemas.registry)
            for kind, schema in self.schemas.by_kind.items()
        }

    # -- issue helpers -------------------------------------------------------

    def _file(self, rel: str) -> str:
        return f"data/{rel}"

    def add(self, rel: str, ptr: str, message: str, rule: str, severity: str = ERROR) -> None:
        issue = Issue(severity, self._file(rel), ptr, message, rule)
        self.report.issues.append(issue)
        if severity == ERROR:
            self.report.invalid_files.add(issue.file)

    # -- JSON Schema with x-vocab / x-ref -----------------------------------

    def _validator_class(self) -> type:
        checker = self
        base_pattern = Draft202012Validator.VALIDATORS["pattern"]

        def x_vocab(validator, name, instance, schema) -> Iterator[ValidationError]:
            if not checker.vocab.has(name):
                yield ValidationError(f"schema refers to unknown vocabulary {name!r}")
                return
            if not isinstance(instance, str):
                return  # the type keyword reports this
            codes = checker.vocab.codes(name)
            if instance not in codes:
                yield ValidationError(
                    f"{instance!r} is not in vocabulary {name!r}{_suggest(instance, codes)}"
                )

        def x_ref(validator, target, instance, schema) -> Iterator[ValidationError]:
            if not isinstance(instance, str):
                return
            message = checker._resolve_ref(target, instance)
            if message:
                yield ValidationError(message)

        def pattern(validator, patrn, instance, schema) -> Iterator[ValidationError]:
            errors = list(base_pattern(validator, patrn, instance, schema))
            yield from errors
            if errors or not isinstance(instance, str) or patrn not in _DATE_PATTERNS:
                return
            try:
                if len(instance) == 10:
                    date.fromisoformat(instance)
                elif len(instance) == 7 and not 1 <= int(instance[5:]) <= 12:
                    raise ValueError
            except ValueError:
                yield ValidationError(f"{instance!r} is not a real calendar date")

        return validators.extend(
            Draft202012Validator, {"x-vocab": x_vocab, "x-ref": x_ref, "pattern": pattern}
        )

    def _resolve_ref(self, target: str, value: str) -> str | None:
        idx = self.index
        if target == "session":
            record = self._current
            jurisdiction = record.data.get("jurisdiction") if record else None
            if not isinstance(jurisdiction, str) or jurisdiction not in idx.by_kind["jurisdiction"]:
                return None  # the jurisdiction ref reports the root cause
            known = idx.sessions.get(jurisdiction, set())
            if value in known:
                return None
            return (
                f"session {value!r} is not listed under sessions in "
                f"data/jurisdictions/{jurisdiction}.yaml{_suggest(value, known)}"
            )
        if target == "any":
            if value in idx.all_ids:
                return None
            return f"no record has id {value!r}{_suggest(value, idx.all_ids)}"
        if target in idx.by_kind:
            ids = idx.by_kind[target]
            if value in ids:
                return None
            return f"no {target} record has id {value!r}{_suggest(value, ids)}"
        return f"schema refers to unknown x-ref target {target!r}"

    def check_schema(self, record: Record) -> None:
        validator = self._validators.get(record.kind)
        if validator is None:
            self.add(record.rel, "", f"no schema for record type {record.kind!r}", "schema")
            return
        self._current = record
        errors = sorted(validator.iter_errors(record.data), key=lambda e: list(e.absolute_path))
        for error in errors:
            ptr = pointer(error.absolute_path)
            if error.validator == "x-ref":
                if error.validator_value == "organization" and not self.strict:
                    # Organization files are added alongside the records that cite
                    # them, so a dangling issuer is expected while a tree fills in.
                    self.add(record.rel, ptr, error.message, "ref-organization", WARNING)
                else:
                    self.add(record.rel, ptr, error.message, "ref")
            elif error.validator == "x-vocab":
                self.add(record.rel, ptr, error.message, "vocab")
            else:
                self.add(record.rel, ptr, error.message, "schema")
        self._current = None

    # -- record rules --------------------------------------------------------

    def check_path(self, record: Record) -> None:
        kind = KINDS[record.kind]
        expected = record.expected_id()
        if expected is None:
            self.add(record.rel, "", f"file must be at data/{kind.path_example}", "id-path")
            return
        if record.id is not None and record.id != expected:
            self.add(
                record.rel, "/id", f"id {record.id!r} does not match path ({expected!r})", "id-path"
            )
        parts = record.path_parts
        data = record.data
        in_jurisdiction_dir = record.kind in ("legislation", "document", "executive-action")
        if in_jurisdiction_dir and data.get("jurisdiction") not in (None, parts[0]):
            self.add(
                record.rel,
                "/jurisdiction",
                f"jurisdiction {data.get('jurisdiction')!r} does not match directory {parts[0]!r}",
                "id-path",
            )
        if record.kind == "legislation":
            session = data.get("session")
            if session is not None and str(session) != parts[1]:
                self.add(
                    record.rel,
                    "/session",
                    f"session {session!r} does not match directory {parts[1]!r}",
                    "id-path",
                )
            ident = data.get("identifier")
            if isinstance(ident, str):
                slug = bill_slug(ident)
                if slug and slug != record.path.stem:
                    self.add(
                        record.rel,
                        "/identifier",
                        f"file name {record.path.stem!r} does not match identifier "
                        f"{ident!r} (expected {slug}.yaml)",
                        "bill-slug",
                        WARNING,
                    )
        if record.kind == "research":
            published = data.get("published")
            if isinstance(published, str) and not published.startswith(parts[0]):
                self.add(
                    record.rel,
                    "/published",
                    f"published {published!r} is not in directory year {parts[0]!r}",
                    "id-path",
                    WARNING,
                )

    def check_duplicates(self) -> None:
        seen: dict[str, Record] = {}
        for record in self.records:
            if record.id is None:
                continue
            first = seen.setdefault(record.id, record)
            if first is not record:
                self.add(
                    record.rel,
                    "/id",
                    f"duplicate id {record.id!r}; also used by {self._file(first.rel)}",
                    "duplicate-id",
                )

    def check_rules(self, record: Record) -> None:
        data = record.data
        rel = record.rel

        today = self.today.isoformat()
        actions = data.get("actions")
        if isinstance(actions, list):
            previous = None
            for i, action in enumerate(actions):
                when = action.get("date") if isinstance(action, dict) else None
                if not isinstance(when, str):
                    continue
                if when > today:
                    self.add(
                        rel,
                        f"/actions/{i}/date",
                        f"{when} is in the future; record actions once the legislature has taken them",
                        "future-date",
                    )
                if previous and when < previous:
                    self.add(
                        rel,
                        f"/actions/{i}/date",
                        f"actions must be in date order: {when} comes after {previous}",
                        "action-order",
                    )
                previous = max(previous or when, when)

        levels = data.get("education_levels")
        provisions = data.get("provisions")
        if isinstance(levels, list) and isinstance(provisions, list):
            types = {p["code"]: p for p in self.vocab.provision_types}
            for i, prov in enumerate(provisions):
                if not isinstance(prov, dict):
                    continue
                own = prov.get("education_levels")
                if isinstance(own, list):
                    extra = sorted(set(own) - set(levels))
                    if extra:
                        self.add(
                            rel,
                            f"/provisions/{i}/education_levels",
                            f"{', '.join(extra)} not among the record's education_levels",
                            "provision-levels",
                        )
                effective = own if isinstance(own, list) else levels
                ptype = types.get(prov.get("type"))
                if ptype:
                    outside = sorted(set(effective) - set(ptype.get("education_levels") or []))
                    if outside:
                        self.add(
                            rel,
                            f"/provisions/{i}/type",
                            f"provision type {ptype['code']!r} is defined for "
                            f"{', '.join(ptype['education_levels'])}, not {', '.join(outside)}",
                            "provision-levels",
                            WARNING,
                        )

        sources = data.get("sources")
        if isinstance(sources, list) and sources:
            kinds = {s.get("kind") for s in sources if isinstance(s, dict)}
            if "official" not in kinds:
                self.add(
                    rel, "/sources", "no source has kind 'official'", "official-source", WARNING
                )

        for container, key in _OBSERVED_FIELDS:
            value = data.get(container)
            items = value if isinstance(value, list) else [value]
            for i, item in enumerate(items):
                if isinstance(item, dict) and isinstance(item.get(key), str) and item[key] > today:
                    ptr = (
                        f"/{container}/{i}/{key}"
                        if isinstance(value, list)
                        else f"/{container}/{key}"
                    )
                    self.add(rel, ptr, f"{item[key]} is in the future", "future-date")

        if record.kind == "document":
            if data.get("status") == "superseded" and not data.get("superseded_by"):
                self.add(
                    rel,
                    "/status",
                    "superseded document has no superseded_by",
                    "superseded",
                    WARNING,
                )
            if data.get("superseded_by") == data.get("id"):
                self.add(rel, "/superseded_by", "document cannot supersede itself", "superseded")
            self.check_document_dates(rel, data, today)

        if record.kind == "executive-action":
            signed, ended = data.get("signed"), data.get("ended")
            if isinstance(signed, str) and isinstance(ended, str) and ended < signed:
                self.add(rel, "/ended", f"ended {ended} is before signed {signed}", "date-order")
            expires = data.get("expires")
            if isinstance(expires, str):
                if isinstance(signed, str) and expires < signed:
                    self.add(
                        rel,
                        "/expires",
                        f"expires {expires} is before signed {signed}",
                        "date-order",
                    )
                if data.get("status") == "active" and expires < today:
                    self.add(
                        rel,
                        "/status",
                        f"status is active but the order was scheduled to expire on {expires}",
                        "expired",
                        WARNING,
                    )

        if record.kind == "jurisdiction":
            seen: set[str] = set()
            for i, s in enumerate(data.get("sessions") or []):
                if not isinstance(s, dict):
                    continue
                ident = str(s.get("identifier"))
                if ident in seen:
                    self.add(
                        rel,
                        f"/sessions/{i}/identifier",
                        f"duplicate session {ident!r}",
                        "duplicate-id",
                    )
                seen.add(ident)
                for j, src in enumerate(s.get("sources") or []):
                    accessed = src.get("accessed") if isinstance(src, dict) else None
                    if isinstance(accessed, str) and accessed > today:
                        self.add(
                            rel,
                            f"/sessions/{i}/sources/{j}/accessed",
                            f"{accessed} is in the future",
                            "future-date",
                        )
                start, end = s.get("start_date"), s.get("end_date")
                if isinstance(start, str) and isinstance(end, str) and end < start:
                    self.add(
                        rel,
                        f"/sessions/{i}/end_date",
                        f"end_date {end} is before start_date {start}",
                        "date-order",
                    )

        related = data.get("related")
        if isinstance(related, list) and data.get("id") in related:
            self.add(
                rel,
                f"/related/{related.index(data['id'])}",
                "record lists itself as related",
                "ref",
            )

    def check_document_dates(self, rel: str, data: dict[str, Any], today: str) -> None:
        published, updated = data.get("published"), data.get("updated")
        dated = [k for k in ("published", "updated") if isinstance(data.get(k), str)]
        if data.get("undated") is True and dated:
            self.add(
                rel,
                "/undated",
                f"undated is true but {' and '.join(dated)} is set",
                "document-date",
            )
        if not dated and data.get("undated") is not True:
            self.add(
                rel,
                "",
                "no published or updated date; set undated: true if the issuer gives none",
                "document-date",
                WARNING,
            )
        for key in dated:
            # A partial date compares as its first day, so '2026-10' is not after 2026-10-17.
            if data[key] > today:
                self.add(rel, f"/{key}", f"{data[key]} is in the future", "future-date")
        if isinstance(published, str) and isinstance(updated, str):
            # Compare at the precision both share, so '2024-06' is not before '2024-06-15'.
            n = min(len(published), len(updated))
            if updated[:n] < published[:n]:
                self.add(
                    rel,
                    "/updated",
                    f"updated {updated} is before published {published}",
                    "date-order",
                )

    def check_vocab(self) -> None:
        topics = self.vocab.codes("topics")
        seen_topics: set[str] = set()
        for i, t in enumerate(self.vocab.topics):
            code = t.get("code")
            if code in seen_topics:
                self.add("vocab/topics.yaml", f"/{i}/code", f"duplicate code {code!r}", "vocab")
            seen_topics.add(code)
        seen_types: set[str] = set()
        for i, p in enumerate(self.vocab.provision_types):
            code = p.get("code")
            if code in seen_types:
                self.add(
                    "vocab/provision-types.yaml", f"/{i}/code", f"duplicate code {code!r}", "vocab"
                )
            seen_types.add(code)
            if p.get("topic") not in topics:
                self.add(
                    "vocab/provision-types.yaml",
                    f"/{i}/topic",
                    f"{p.get('topic')!r} is not a topic code",
                    "vocab",
                )
        levels = self.vocab.codes("education_levels")
        for name, entries in (
            ("topics", self.vocab.topics),
            ("provision-types", self.vocab.provision_types),
        ):
            for i, entry in enumerate(entries):
                bad = sorted(set(entry.get("education_levels") or []) - levels)
                if bad:
                    self.add(
                        f"vocab/{name}.yaml", f"/{i}/education_levels", f"unknown {bad}", "vocab"
                    )

    def run(self) -> Report:
        for issue in self.records.issues:
            self.add(issue.rel, "", issue.message, "load", issue.severity)
        self.check_vocab()
        self.check_duplicates()
        for record in self.records:
            self.check_path(record)
            self.check_schema(record)
            self.check_rules(record)
        self.report.issues.sort(key=lambda i: (i.file, i.pointer, i.severity, i.message))
        return self.report


def bill_slug(identifier: str) -> str | None:
    """The file-name slug the brief's convention gives a bill number.

    'HB 1481' -> 'hb-1481', 'H.R. 610' -> 'hr-610', 'H.J.R. 5' -> 'hjr-5'.
    Returns None for identifiers that do not look like prefix + number.
    """
    match = re.fullmatch(r"\s*([A-Za-z][A-Za-z.\s]*?)\s*(\d+[A-Za-z]?)\s*", identifier)
    if not match:
        return None
    prefix = re.sub(r"[^a-z]", "", match.group(1).lower())
    return f"{prefix}-{match.group(2).lower()}" if prefix else None


def validate(
    paths: Paths,
    *,
    strict: bool = False,
    today: date | None = None,
    records: RecordSet | None = None,
) -> tuple[Report, RecordSet]:
    records = records if records is not None else load_records(paths.data)
    checker = _Checker(paths, records, strict=strict, today=today or date.today())
    return checker.run(), records
