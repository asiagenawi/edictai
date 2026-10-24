"""Check judgment files written by reviewers before anything is imported or scored."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..vocab import Vocab
from .common import read_jsonl
from .instructions import (
    FULL_REASON_WORDS,
    LOADED_WORDS,
    SUMMARY_MAX_SENTENCES,
    TRIAGE_REASON_WORDS,
    full_schema,
    triage_schema,
)
from .texts import TextStore
from .textutil import quote_in_text


def words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def sentences(text: str) -> int:
    # Ends of sentences: . ! ? followed by space and a capital or end of text,
    # ignoring common abbreviations and section references.
    cleaned = re.sub(
        r"\b(?:Sec|Secs|No|Nos|U\.S|e\.g|i\.e|etc|Inc|St|Dr|Mr|Ms|Mrs|H\.R|S|Rev|Stat|art|ch|"
        r"subd|para|cl)\.",
        "_",
        text or "",
    )
    cleaned = re.sub(r"\b[A-Z]\.", "_", cleaned)
    return max(1, len(re.findall(r"[.!?](?:\s+(?=[A-Z\"'(])|\s*$)", cleaned.strip())))


@dataclass
class Checked:
    """One judgment after checking."""

    key: str
    row: dict[str, Any]
    errors: list[str] = field(default_factory=list)  # reject the judgment
    warnings: list[str] = field(default_factory=list)
    quote_failures: list[int] = field(default_factory=list)  # provision indexes
    text_available: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class CheckReport:
    stage: str
    items: list[Checked] = field(default_factory=list)
    file_errors: list[str] = field(default_factory=list)  # duplicate/missing keys etc.

    @property
    def ok(self) -> bool:
        return not self.file_errors and all(c.ok and not c.quote_failures for c in self.items)

    def by_key(self) -> dict[str, Checked]:
        return {c.key: c for c in self.items}

    def lines(self) -> list[str]:
        out = list(self.file_errors)
        for c in self.items:
            out += [f"{c.key}: error: {e}" for e in c.errors]
            out += [f"{c.key}: warning: {w}" for w in c.warnings]
        return out

    def summary(self) -> str:
        errors = sum(len(c.errors) for c in self.items) + len(self.file_errors)
        quotes = sum(len(c.quote_failures) for c in self.items)
        return (
            f"{len(self.items)} judgments, {errors} errors, "
            f"{quotes} unverified quotes, {sum(len(c.warnings) for c in self.items)} warnings"
        )


def _schema_errors(validator: Draft202012Validator, row: Mapping[str, Any]) -> list[str]:
    out = []
    for e in sorted(validator.iter_errors(row), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in e.absolute_path) or "(line)"
        out.append(f"{where}: {e.message}")
    return out


def _file_checks(
    report: CheckReport, rows: list[dict[str, Any]], expected: list[str] | None
) -> list[dict[str, Any]]:
    seen: dict[str, int] = {}
    unique = []
    for row in rows:
        key = row.get("key")
        if not isinstance(key, str):
            report.file_errors.append(f"line without a string key: {str(row)[:80]}")
            continue
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            report.file_errors.append(f"{key}: appears more than once")
        if seen[key] == 1:
            unique.append(row)
    if expected is not None:
        want = set(expected)
        for key in expected:
            if key not in seen:
                report.file_errors.append(f"{key}: missing from the output")
        for key in seen:
            if key not in want:
                report.file_errors.append(f"{key}: not in the input batch")
        got = [r["key"] for r in unique if r["key"] in want]
        if not report.file_errors and got != [k for k in expected if k in seen]:
            report.file_errors.append("judgments are not in the input order")
    return unique


def check_triage(rows: list[dict[str, Any]], expected: list[str] | None = None) -> CheckReport:
    report = CheckReport("triage")
    validator = Draft202012Validator(triage_schema())
    for row in _file_checks(report, rows, expected):
        c = Checked(row["key"], row, errors=_schema_errors(validator, row))
        if isinstance(row.get("reason"), str) and words(row["reason"]) > TRIAGE_REASON_WORDS:
            c.errors.append(f"reason has {words(row['reason'])} words; the limit is 20")
        report.items.append(c)
    return report


def check_full(
    rows: list[dict[str, Any]],
    vocab: Vocab,
    store: TextStore,
    expected: list[str] | None = None,
    federal: bool = False,
) -> CheckReport:
    """Schema, vocabulary, summary, locator, and verbatim-quote checks.

    A quote is verified when it is found in any stored version of the bill:
    the latest, or the earlier version fetched for amended-out AI language.
    """
    report = CheckReport("full")
    validator = Draft202012Validator(full_schema(vocab, federal))
    ptypes = {p["code"]: p for p in vocab.provision_types}
    for row in _file_checks(report, rows, expected):
        c = Checked(row["key"], row, errors=_schema_errors(validator, row))
        report.items.append(c)
        if c.errors:
            continue
        reasons = row.get("reasons") or ""
        if words(reasons) > FULL_REASON_WORDS:
            c.errors.append(f"reasons has {words(reasons)} words; the limit is {FULL_REASON_WORDS}")
        meta, _ = store.for_key(c.key)
        # A quote may come from the latest version or the earlier one stored beside it.
        texts = [t for _, t in store.texts_for_key(c.key)]
        c.text_available = bool(texts)
        if not row["in_scope"]:
            continue
        if federal:
            is_document = c.key.startswith("us-fr-")
            if is_document and not row.get("classification"):
                c.errors.append("classification is required for a Federal Register document")
            elif row.get("classification") and not is_document:
                c.errors.append("classification is only for Federal Register documents")
        summary = row.get("summary") or ""
        n = sentences(summary)
        if n > SUMMARY_MAX_SENTENCES:
            c.errors.append(f"summary has {n} sentences; the limit is {SUMMARY_MAX_SENTENCES}")
        loaded = sorted(
            {w for w in LOADED_WORDS if re.search(rf"\b{re.escape(w)}\b", summary, re.I)}
        )
        if loaded:
            c.errors.append(f"summary uses characterizing words: {', '.join(loaded)}")
        levels = set(row.get("education_levels") or [])
        for i, prov in enumerate(row.get("provisions") or []):
            own = set(prov.get("education_levels") or [])
            if own - levels:
                c.errors.append(
                    f"provisions/{i}: education_levels {sorted(own - levels)} "
                    "not among the bill's education_levels"
                )
            if not prov.get("locator", "").strip():
                c.errors.append(f"provisions/{i}: locator is empty")
            ptype = ptypes.get(prov.get("type"))
            effective = own or levels
            if ptype and effective - set(ptype.get("education_levels") or []):
                c.warnings.append(
                    f"provisions/{i}: type {ptype['code']} is defined for "
                    f"{', '.join(ptype['education_levels'])}"
                )
            quote = prov.get("quote") or ""
            if "..." in quote or "\u2026" in quote:
                c.errors.append(f"provisions/{i}: quote contains an ellipsis")
                c.quote_failures.append(i)
            elif not texts:
                c.quote_failures.append(i)
                c.errors.append(f"provisions/{i}: no stored text to verify the quote against")
            elif not any(quote_in_text(quote, t) for t in texts):
                c.quote_failures.append(i)
                c.errors.append(f"provisions/{i}: quote not found verbatim in the stored text")
        if meta is None:
            c.warnings.append("no text has been fetched for this bill")
    return report


def load_rows(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(read_jsonl(path))
    return rows


def batch_for_output(out_path: Path) -> Path:
    """`.../{stage}/out/batch-001.jsonl` -> `.../{stage}/batch-001.jsonl`."""
    return out_path.parent.parent / out_path.name


def expected_keys(batch_path: Path) -> list[str] | None:
    if not batch_path.exists():
        return None
    return [r["key"] for r in read_jsonl(batch_path)]
