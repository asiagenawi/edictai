"""`classify refresh`: add newly recorded actions and text versions to existing bills.

State bills (records with `external_ids.legiscan_bill_id`) are compared with
the bill in the cached LegiScan data: the session dataset that `ingest
legiscan` keeps current, or a cached getBill response when that is newer.
Federal bills (records with `external_ids.congress_gov`, or federal records
without a LegiScan id) are compared with the Congress.gov row for the bill in
the newest federal candidate file, which `ingest federal` writes with every
known record's actions. No request is sent.

Actions are mapped exactly as `classify import` maps them (`actions.map_history`
for LegiScan, `federal_import.map_actions` for Congress.gov). An action is
added when it is dated after the record's last action, or on that date when
every action the record has for that date is in the source's list (the
record came from the same source). Versions are added only to a record whose
listed versions all appear in the source. Nothing else in a record changes:
the new items are appended to the end of its `actions` and `versions` lists
and the rest of the file is left as it was. Status is derived from actions,
so the new actions are what move it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..paths import Paths
from ..status import derive_status
from ..yamlio import load_yaml
from .actions import map_history
from .common import BillStore, ClassifyPaths, latest_candidates, read_jsonl
from .yamlwrite import dump


@dataclass
class Update:
    id: str
    path: Path
    source: str  # legiscan or congress
    added_actions: list[dict[str, Any]] = field(default_factory=list)
    added_versions: list[dict[str, Any]] = field(default_factory=list)
    status_before: str | None = None
    status_after: str | None = None

    def as_dict(self, root: Path) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": str(self.path.relative_to(root)) if self.path.is_absolute() else str(self.path),
            "source": self.source,
            "added_actions": self.added_actions,
            "added_versions": self.added_versions,
            "status_before": self.status_before,
            "status_after": self.status_after,
        }


@dataclass
class RefreshResult:
    updates: list[Update] = field(default_factory=list)
    checked: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def as_dict(self, root: Path) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "updated": [u.as_dict(root) for u in self.updates],
            "skipped": [{"id": i, "why": w} for i, w in self.skipped],
        }


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().rstrip(".").lower()


def new_actions(
    existing: list[Mapping[str, Any]], fresh: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The actions of `fresh` to append to `existing` (see the module docstring)."""
    fresh = [dict(a) for a in fresh]
    if not existing:
        return []
    last = max(str(a.get("date") or "") for a in existing)
    have = {(str(a.get("date")), _norm(a.get("description"))) for a in existing}
    on_last = {_norm(a.get("description")) for a in existing if str(a.get("date")) == last}
    fresh_last = {_norm(a.get("description")) for a in fresh if str(a.get("date")) == last}
    same_source = on_last <= fresh_last
    out: list[dict[str, Any]] = []
    for a in fresh:
        d = str(a.get("date") or "")
        sig = (d, _norm(a.get("description")))
        if sig in have:
            continue
        if d > last or (d == last and same_source):
            have.add(sig)
            out.append(a)
    return out


def new_versions(
    existing: list[Mapping[str, Any]] | None, fresh: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    if not existing:
        return []
    have = {v.get("url") for v in existing}
    fresh = [dict(v) for v in fresh]
    if not have <= {v.get("url") for v in fresh}:
        return []
    return [v for v in fresh if v.get("url") not in have]


def append_to_list(text: str, key: str, items: list[dict[str, Any]]) -> str | None:
    """`text` with `items` appended to the top-level block list `key`, every other line
    unchanged; None when the file does not have that list in the record style."""
    if not items:
        return text
    lines = text.rstrip("\n").split("\n")
    try:
        top = lines.index(f"{key}:")
    except ValueError:
        return None
    end = len(lines)
    for i in range(top + 1, len(lines)):
        if lines[i] and not lines[i].startswith(" "):
            end = i
            break
    while end > top + 1 and not lines[end - 1].strip():
        end -= 1
    if not any(ln.startswith("  - ") for ln in lines[top + 1 : end]):
        return None
    block = dump({key: items}).rstrip("\n").split("\n")[1:]
    return "\n".join([*lines[:end], *block, *lines[end:]]) + "\n"


class _Sessions:
    def __init__(self, data: Path):
        self.data = data
        self._ends: dict[str, dict[str, str | None]] = {}

    def end(self, jurisdiction: str, session: str) -> str | None:
        if jurisdiction not in self._ends:
            path = self.data / "jurisdictions" / f"{jurisdiction}.yaml"
            doc = (load_yaml(path) or {}) if path.exists() else {}
            self._ends[jurisdiction] = {
                str(s.get("identifier")): s.get("end_date") for s in doc.get("sessions") or []
            }
        return self._ends[jurisdiction].get(session)


def _status(record: Mapping[str, Any], actions: list[Mapping[str, Any]], sessions, today) -> str:
    return derive_status(
        actions,
        jurisdiction=str(record.get("jurisdiction") or ""),
        classification=str(record.get("classification") or "bill"),
        session_end=sessions.end(str(record.get("jurisdiction")), str(record.get("session"))),
        today=today,
    ).status


def _apply(
    path: Path,
    record: Mapping[str, Any],
    source: str,
    fresh_actions: list[dict[str, Any]],
    fresh_versions: list[dict[str, Any]],
    sessions: _Sessions,
    today: date,
    result: RefreshResult,
    dry_run: bool,
) -> None:
    rid = str(record.get("id") or path.stem)
    existing = list(record.get("actions") or [])
    fresh_actions = [a for a in fresh_actions if str(a.get("date")) <= today.isoformat()]
    acts = new_actions(existing, fresh_actions)
    vers = new_versions(record.get("versions"), fresh_versions)
    if not acts and not vers:
        return
    text = path.read_text(encoding="utf-8")
    updated = append_to_list(text, "actions", acts) if acts else text
    if updated is not None and vers:
        updated = append_to_list(updated, "versions", vers)
    if updated is None:
        result.skipped.append((rid, "actions or versions are not a block list in the record style"))
        return
    reread = load_yaml_text(updated)
    if list(reread.get("actions") or []) != [*existing, *acts]:
        result.skipped.append((rid, "the edited file did not read back as expected"))
        return
    update = Update(
        rid,
        path,
        source,
        acts,
        vers,
        _status(record, existing, sessions, today),
        _status(record, [*existing, *acts], sessions, today),
    )
    if not dry_run:
        path.write_text(updated, encoding="utf-8")
    result.updates.append(update)


def load_yaml_text(text: str) -> dict[str, Any]:
    import yaml

    from ..yamlio import RecordLoader

    return yaml.load(text, Loader=RecordLoader) or {}


def _records(data: Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    for path in sorted((data / "legislation").glob("*/*/*.yaml")):
        try:
            record = load_yaml(path)
        except Exception:  # an unreadable record is the validator's problem
            continue
        if isinstance(record, dict):
            yield path, record


def _source(record: Mapping[str, Any]) -> str | None:
    ext = record.get("external_ids") or {}
    if ext.get("congress_gov"):
        return "congress"
    if ext.get("legiscan_bill_id"):
        return "legiscan"
    if record.get("jurisdiction") == "us":
        return "congress"
    return None


def _fresher(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any] | None:
    """The copy of a LegiScan bill with the later (then longer) history."""

    def key(bill: Mapping[str, Any]) -> tuple[str, int]:
        dates = [str(h.get("date") or "") for h in bill.get("history") or []]
        return (max(dates, default=""), len(dates))

    found = [x for x in (a, b) if x]
    return max(found, key=key) if found else None


def refresh(
    paths: Paths,
    *,
    today: date,
    sources: Iterable[str] = ("legiscan", "congress"),
    federal_candidates: Path | None = None,
    dry_run: bool = False,
) -> RefreshResult:
    from ..ingest.fulltext import dataset_bill_index
    from .federal_import import map_actions
    from .federal_import import map_versions as federal_versions
    from .importer import map_versions as legiscan_versions

    sources = set(sources)
    result = RefreshResult()
    sessions = _Sessions(paths.data)
    cp = ClassifyPaths(paths)
    federal_rows: dict[str, dict[str, Any]] = {}
    if "congress" in sources:
        try:
            fpath = federal_candidates or latest_candidates(ClassifyPaths(paths, federal=True))
        except FileNotFoundError:
            fpath = None
        for row in read_jsonl(fpath) if fpath else []:
            if row.get("source") == "congress" and row.get("existing_record"):
                federal_rows[str(row["existing_record"])] = row
    store = None
    if "legiscan" in sources and (cp.legiscan / "datasets").is_dir():
        dataset_bill_index(cp.legiscan)  # brings the bill index up to the cached datasets
        store = BillStore(cp.legiscan)
    try:
        for path, record in _records(paths.data):
            source = _source(record)
            if source not in sources:
                continue
            rid = str(record.get("id") or path.stem)
            if source == "legiscan":
                if store is None:
                    continue
                bill_id = int(record["external_ids"]["legiscan_bill_id"])
                bill = _fresher(store.from_dataset(bill_id), store.from_response(bill_id))
                if bill is None:
                    result.skipped.append((rid, "bill not in the cached LegiScan data"))
                    continue
                result.checked += 1
                fresh = map_history(
                    bill.get("history") or [],
                    bill.get("progress") or [],
                    jurisdiction=str(record["jurisdiction"]),
                )
                versions = legiscan_versions(bill.get("texts") or [])
            else:
                row = federal_rows.get(rid)
                if row is None:
                    continue
                result.checked += 1
                fresh = map_actions(row, today)
                versions = federal_versions(row)
            _apply(path, record, source, fresh, versions, sessions, today, result, dry_run)
    finally:
        if store is not None:
            store.close()
    return result
