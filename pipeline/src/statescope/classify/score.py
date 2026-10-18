"""Score judgments against the hand-labeled evaluation set.

Judgments are matched to evaluation entries by id; when the id differs only
in how the bill number is written (Colorado 'SB 205' against 'SB 24-205'),
by jurisdiction, session, and bill-number key; and failing that by
jurisdiction and bill-number key when exactly one entry in an overlapping
session has it (a two-year legislature's bill against an annual label).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..ingest.bill_numbers import match_key
from ..yamlio import load_yaml

POSITIVE = "in_scope"


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, truth: bool, predicted: bool) -> None:
        if truth and predicted:
            self.tp += 1
        elif truth:
            self.fn += 1
        elif predicted:
            self.fp += 1
        else:
            self.tn += 1

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or p + r == 0:
            return None
        return 2 * p * r / (p + r)

    @property
    def accuracy(self) -> float | None:
        return (self.tp + self.tn) / self.n if self.n else None

    def as_dict(self) -> dict[str, Any]:
        def r(x: float | None) -> float | None:
            return None if x is None else round(x, 3)

        return {
            "n": self.n,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "precision": r(self.precision),
            "recall": r(self.recall),
            "f1": r(self.f1),
            "accuracy": r(self.accuracy),
        }


def predicted_in(row: Mapping[str, Any], needs_text_as_in: bool = True) -> bool:
    """Scope prediction from a full judgment (`in_scope`) or a triage one (`decision`)."""
    if "in_scope" in row:
        return bool(row["in_scope"])
    decision = row.get("decision")
    return decision == "in" or (needs_text_as_in and decision == "needs_text")


def _split_key(key: str) -> tuple[str, str] | None:
    """(jurisdiction, rest) of a key."""
    parts = key.split("-")
    if parts[0] != "us":
        return None
    if len(parts) > 1 and len(parts[1]) == 2 and parts[1].isalpha():
        return f"us-{parts[1]}", "-".join(parts[2:])
    return "us", "-".join(parts[1:])


def _session_and_slug(rest: str) -> tuple[str, str]:
    # Sessions: '2025', '2025-2026', '2025-s1', '119'.
    parts = rest.split("-")
    if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit() and len(parts[1]) == 4:
        return f"{parts[0]}-{parts[1]}", "-".join(parts[2:])
    if (
        len(parts) >= 3
        and parts[0].isdigit()
        and parts[1].startswith("s")
        and parts[1][1:].isdigit()
    ):
        return f"{parts[0]}-{parts[1]}", "-".join(parts[2:])
    return parts[0], "-".join(parts[1:])


def _bill_key(jurisdiction: str, slug_or_identifier: str) -> tuple[str, int] | None:
    return match_key(jurisdiction, slug_or_identifier.replace("-", " "))


def _years(session: str) -> set[int]:
    """Calendar years a session identifier covers ('119' -> {2025, 2026})."""
    head = session.split("-s")[0]
    nums = [int(x) for x in head.split("-") if x.isdigit()]
    if len(nums) == 1 and nums[0] < 1000:  # a Congress
        start = 1789 + 2 * (nums[0] - 1)
        return {start, start + 1}
    if len(nums) == 2:
        return set(range(nums[0], nums[1] + 1))
    return set(nums)


@dataclass
class Match:
    entry: dict[str, Any]
    judgment: dict[str, Any]
    how: str


def match(
    entries: list[dict[str, Any]],
    judgments: Iterable[Mapping[str, Any]],
    bills: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[Match], list[dict[str, Any]], list[str]]:
    """`bills` maps judgment keys to candidate rows (jurisdiction, session, identifier)."""
    """(matched pairs, eval entries with no judgment, judgment keys not in the set)."""
    by_id = {e["id"]: e for e in entries}
    by_session: dict[tuple[str, str, tuple[str, int] | None], list[dict[str, Any]]] = {}
    by_bill: dict[tuple[str, tuple[str, int] | None], list[dict[str, Any]]] = {}
    for e in entries:
        k = match_key(e["jurisdiction"], e["identifier"])
        by_session.setdefault((e["jurisdiction"], str(e["session"]), k), []).append(e)
        by_bill.setdefault((e["jurisdiction"], k), []).append(e)
    matched: list[Match] = []
    used: set[str] = set()
    unmatched: list[str] = []
    for j in judgments:
        key = str(j.get("key"))
        entry, how = by_id.get(key), "id"
        if entry is None:
            bill = (bills or {}).get(key)
            split = _split_key(key)
            if bill:
                jur, session = str(bill["jurisdiction"]), str(bill["session"])
                bk = match_key(jur, str(bill["identifier"]))
            elif split:
                jur, rest = split
                session, slug = _session_and_slug(rest)
                bk = _bill_key(jur, slug)
            if bill or split:
                hits = by_session.get((jur, session, bk)) or []
                if len(hits) == 1:
                    entry, how = hits[0], "session+number"
                else:
                    years = _years(session)
                    hits = [
                        e for e in by_bill.get((jur, bk)) or [] if _years(str(e["session"])) & years
                    ]
                    if len(hits) == 1:
                        entry, how = hits[0], "number"
        if entry is None or entry["id"] in used:
            unmatched.append(key)
            continue
        used.add(entry["id"])
        matched.append(Match(entry, dict(j), how))
    missing = [e for e in entries if e["id"] not in used]
    return matched, missing, unmatched


@dataclass
class Score:
    overall: Counts = field(default_factory=Counts)
    by_difficulty: dict[str, Counts] = field(default_factory=dict)
    by_level: dict[str, Counts] = field(default_factory=dict)
    level_agreement: dict[str, Counts] = field(default_factory=dict)
    false_positives: list[dict[str, Any]] = field(default_factory=list)
    false_negatives: list[dict[str, Any]] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    matched: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "overall": self.overall.as_dict(),
            "by_difficulty": {k: v.as_dict() for k, v in sorted(self.by_difficulty.items())},
            "recall_by_level": {k: v.as_dict() for k, v in sorted(self.by_level.items())},
            "level_agreement_on_true_positives": {
                k: v.as_dict() for k, v in sorted(self.level_agreement.items())
            },
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "eval_entries_not_judged": len(self.missing),
            "judgments_not_in_eval": len(self.unmatched),
        }


def load_eval(path: Path) -> list[dict[str, Any]]:
    data = load_yaml(path) or []
    return [e for e in data if isinstance(e, dict) and e.get("id")]


def score(
    entries: list[dict[str, Any]],
    judgments: Iterable[Mapping[str, Any]],
    *,
    needs_text_as_in: bool = True,
    missing_as_out: bool = False,
    bills: Mapping[str, Mapping[str, Any]] | None = None,
) -> Score:
    matched, missing, unmatched = match(entries, judgments, bills)
    result = Score(missing=missing, unmatched=unmatched, matched=len(matched))
    pairs = [(m.entry, m.judgment) for m in matched]
    if missing_as_out:
        pairs += [
            (e, {"key": e["id"], "in_scope": False, "reasons": "not a candidate"}) for e in missing
        ]
    for entry, j in pairs:
        truth = entry.get("label") == POSITIVE
        pred = predicted_in(j, needs_text_as_in)
        result.overall.add(truth, pred)
        result.by_difficulty.setdefault(str(entry.get("difficulty")), Counts()).add(truth, pred)
        if truth:
            for level in entry.get("education_levels") or []:
                result.by_level.setdefault(level, Counts()).add(truth, pred)
            if pred and j.get("education_levels") is not None:
                predicted_levels = set(j.get("education_levels") or [])
                for level in ("k12", "postsecondary"):
                    result.level_agreement.setdefault(level, Counts()).add(
                        level in (entry.get("education_levels") or []), level in predicted_levels
                    )
        if truth != pred:
            row = {
                "id": entry["id"],
                "judgment_key": j.get("key"),
                "difficulty": entry.get("difficulty"),
                "label": entry.get("label"),
                "rationale": entry.get("rationale"),
                "judgment": j.get("reasons") or j.get("reason"),
            }
            (result.false_negatives if truth else result.false_positives).append(row)
    return result


def format_report(s: Score) -> str:
    def line(name: str, c: Counts) -> str:
        d = c.as_dict()
        fmt = lambda x: "  -  " if x is None else f"{x:.3f}"  # noqa: E731
        return (
            f"  {name:<22} n={d['n']:<4} P={fmt(c.precision)} R={fmt(c.recall)} "
            f"F1={fmt(c.f1)}  (tp {c.tp}, fp {c.fp}, fn {c.fn}, tn {c.tn})"
        )

    out = [f"Matched {s.matched} judgments to the evaluation set"]
    out.append(line("overall", s.overall))
    for k, c in sorted(s.by_difficulty.items()):
        out.append(line(f"difficulty {k}", c))
    for k, c in sorted(s.by_level.items()):
        out.append(line(f"recall, level {k}", c))
    for k, c in sorted(s.level_agreement.items()):
        out.append(line(f"level tag {k}", c))
    if s.false_positives:
        out.append("False positives (judged in, labeled out):")
        out += [
            f"  {r['id']} [{r['difficulty']}]: {r['judgment']} | label: {r['rationale']}"
            for r in s.false_positives
        ]
    if s.false_negatives:
        out.append("False negatives (judged out, labeled in):")
        out += [
            f"  {r['id']} [{r['difficulty']}]: {r['judgment']} | label: {r['rationale']}"
            for r in s.false_negatives
        ]
    out.append(
        f"{len(s.missing)} evaluation entries have no judgment; "
        f"{len(s.unmatched)} judgments are not in the evaluation set"
    )
    return "\n".join(out)
