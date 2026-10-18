"""Shared helpers: bill keys, candidate files, batch files, and cached LegiScan bills."""

from __future__ import annotations

import json
import os
import re
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..paths import Paths
from ..validate import bill_slug

STAGES = ("triage", "full")


@dataclass(frozen=True)
class ClassifyPaths:
    """Where the classifier tooling keeps its working files (all under .cache/)."""

    paths: Paths
    # Batches, judgments, and instructions; texts and LegiScan responses stay shared.
    work: Path | None = None

    @property
    def root(self) -> Path:
        return self.work or self.paths.cache / "classify"

    def stage_dir(self, stage: str) -> Path:
        return self.root / stage

    def out_dir(self, stage: str) -> Path:
        return self.stage_dir(stage) / "out"

    def instructions(self, stage: str) -> Path:
        return self.root / f"{stage.upper()}.md"

    def output_schema(self, stage: str) -> Path:
        return self.root / f"{stage}.schema.json"

    @property
    def legiscan(self) -> Path:
        return self.paths.cache / "legiscan"

    @property
    def texts(self) -> Path:
        return self.paths.cache / "texts"

    @property
    def candidates(self) -> Path:
        return self.paths.cache / "candidates"


# -- keys ---------------------------------------------------------------------


def slug_for(identifier: str) -> str:
    """The file-name slug for a bill number; falls back to a hyphenated form.

    'HB 1481' -> 'hb-1481'. Numbers the record convention does not cover
    ('B26-0491') become lowercase, hyphen-separated, without leading zeros:
    'b26-491'.
    """
    slug = bill_slug(identifier)
    if slug:
        return slug
    parts = re.findall(r"[A-Za-z]+\d*|\d+", identifier)
    cleaned = []
    for part in parts:
        m = re.fullmatch(r"([A-Za-z]*)(\d*)", part)
        letters, digits = (m.group(1), m.group(2)) if m else (part, "")
        cleaned.append(letters.lower() + (str(int(digits)) if digits else ""))
    return "-".join(p for p in cleaned if p)


def bill_key(jurisdiction: str, session: str, identifier: str) -> str:
    """`{jurisdiction}-{session}-{bill-slug}`: the record id the bill would get."""
    return f"{jurisdiction}-{session}-{slug_for(identifier)}"


def candidate_key(row: Mapping[str, Any]) -> str:
    return bill_key(str(row["jurisdiction"]), str(row["session"]), str(row["identifier"]))


# -- JSON lines ---------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path}:{n}: not valid JSON ({exc})") from None
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{n}: each line must be a JSON object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def expand(patterns: Iterable[str | Path]) -> list[Path]:
    """Paths, with directories and shell-style globs expanded to their .jsonl files."""
    found: list[Path] = []
    for pattern in patterns:
        p = Path(pattern)
        if p.is_dir():
            found.extend(sorted(p.glob("*.jsonl")))
        elif any(ch in str(pattern) for ch in "*?["):
            anchor = Path(p.anchor) if p.is_absolute() else Path()
            rel = p.relative_to(anchor) if p.is_absolute() else p
            found.extend(sorted(anchor.glob(str(rel))))
        else:
            found.append(p)
    return found


# -- candidates ---------------------------------------------------------------


def latest_candidates(cp: ClassifyPaths) -> Path:
    files = sorted(
        p for p in cp.candidates.glob("*.jsonl") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem)
    )
    if not files:
        raise FileNotFoundError(
            f"no candidate files in {cp.candidates}; run `statescope ingest candidates` first"
        )
    return files[-1]


def _last_activity(row: Mapping[str, Any]) -> str:
    dates = [h.get("date") or "" for h in row.get("history") or []]
    return max([*dates, row.get("status_date") or ""])


def load_candidates(path: Path) -> dict[str, dict[str, Any]]:
    """key -> candidate row, one per key.

    A bill carried over between the annual datasets of a two-year
    legislature appears once per dataset under different bill_ids; the row
    with the latest activity is kept and the others' ids are listed under
    `other_bill_ids`.
    """
    out: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        key = candidate_key(row)
        row = {**row, "key": key}
        prior = out.get(key)
        if prior is None:
            out[key] = row
            continue
        keep, drop = sorted(
            (prior, row), key=lambda r: (_last_activity(r), r.get("bill_id") or 0), reverse=True
        )
        others = {*(prior.get("other_bill_ids") or []), *(row.get("other_bill_ids") or [])}
        others.add(drop["bill_id"])
        others.discard(keep["bill_id"])
        out[key] = {**keep, "other_bill_ids": sorted(others)}
    return out


# -- full LegiScan bills from the cache ---------------------------------------


class BillStore:
    """Full LegiScan bill objects from the cached datasets or getBill responses.

    Reads local files only; never sends a query.
    """

    def __init__(self, legiscan_cache: Path):
        self.cache = legiscan_cache
        self._index: dict[int, tuple[int, str]] | None = None
        self._zips: dict[int, zipfile.ZipFile] = {}

    def _bill_index(self) -> dict[int, tuple[int, str]]:
        if self._index is None:
            index: dict[int, tuple[int, str]] = {}
            for part in sorted((self.cache / "bill_index").glob("*.json")):
                data = read_json(part)
                for bid, name in (data.get("bills") or {}).items():
                    index[int(bid)] = (int(part.stem), name)
            self._index = index
        return self._index

    def get(self, bill_id: int) -> dict[str, Any] | None:
        path = self.cache / "responses" / "getBill" / f"{bill_id}.json"
        if path.exists():
            return read_json(path)["response"]["bill"]
        hit = self._bill_index().get(int(bill_id))
        if hit is None:
            return None
        sid, name = hit
        archive = self.cache / "datasets" / f"{sid}.zip"
        if not archive.exists():
            return None
        zf = self._zips.get(sid)
        if zf is None:
            zf = self._zips[sid] = zipfile.ZipFile(archive)
        try:
            return json.loads(zf.read(name)).get("bill")
        except KeyError:
            return None

    def session_bills(self, session_id: int) -> Iterator[dict[str, Any]]:
        archive = self.cache / "datasets" / f"{session_id}.zip"
        if not archive.exists():
            return
        from ..ingest.candidates import iter_bills

        yield from iter_bills(archive)

    def close(self) -> None:
        for zf in self._zips.values():
            zf.close()
        self._zips.clear()


def real_date(value: Any) -> str | None:
    """A YYYY-MM-DD date, or None for LegiScan's '0000-00-00' placeholder."""
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) and value[:4] != "0000":
        return value
    return None


def latest_text(texts: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The most recent bill text: latest date, then highest doc_id."""
    rows = [dict(t) for t in texts if t.get("doc_id")]
    if not rows:
        return None
    return max(rows, key=lambda t: (real_date(t.get("date")) or "", int(t["doc_id"])))
