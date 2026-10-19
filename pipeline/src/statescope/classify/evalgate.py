"""`classify eval-gate`: score the automated classifier on the evaluation set.

The classifier is the pair of instruction files and schemas for each stage
(state and federal), the text wrapped around the items, the model, and the
effort. A SHA-256 of all of them keys the stored result in
`.cache/classify/eval-gate.json`, so the evaluation runs again only when one
of them changes. A run classifies every evaluation entry found among the
current candidates (triage, then full for the bills triaged in or
needs_text) in its own working directory under `.cache/classify/eval/`, with
the stored texts and no new text requests, and scores the result with
`classify score`'s matching. The gate fails when precision or recall is below
the floors in pipeline/automation.yaml, or when the budget ran out before every
entry was judged.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..paths import Paths
from ..vocab import Vocab
from .common import (
    BillStore,
    ClassifyPaths,
    latest_candidates,
    load_candidates,
    read_json,
    read_jsonl,
    write_json,
)
from .instructions import write_instructions
from .llm import Judge
from .prep import PrepResult, assigned_keys, judged_keys, triage_decisions, write_batches
from .run import judge_pending, prompt_templates, stage_spec
from .score import load_eval, match, score
from .settings import Settings
from .texts import TextStore

FILES = ("TRIAGE.md", "FULL.md", "triage.schema.json", "full.schema.json")


def gate_path(paths: Paths) -> Path:
    return paths.cache / "classify" / "eval-gate.json"


def eval_path(paths: Paths) -> Path:
    return paths.root / "pipeline" / "eval" / "classifier_eval.yaml"


def gate_hash(paths: Paths, settings: Settings) -> str:
    """SHA-256 of the instructions and schemas (state and federal, regenerated first),
    the prompt templates, the model, and the effort."""
    h = hashlib.sha256()
    for federal in (False, True):
        cp = ClassifyPaths(paths, federal=federal)
        write_instructions(paths, cp.root, federal=federal)
        for name in FILES:
            h.update(f"{'federal' if federal else 'state'}/{name}\0".encode())
            h.update((cp.root / name).read_bytes())
    h.update(prompt_templates().encode())
    h.update(f"\0model={settings.model}\0effort={settings.effort}".encode())
    return h.hexdigest()


@dataclass
class GateResult:
    hash: str
    record: dict[str, Any]
    ran: bool
    passed: bool
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "hash": self.hash,
            "ran": self.ran,
            "passed": self.passed,
            "reasons": self.reasons,
            **self.record,
        }


def _items(
    cp: ClassifyPaths, prod: ClassifyPaths, entries: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Candidates of the production working directory that match an evaluation entry."""
    try:
        candidates = load_candidates(latest_candidates(prod))
    except FileNotFoundError:
        return {}
    if not cp.federal:
        # LegiScan's copies of federal bills are scored from the federal candidates.
        candidates = {k: v for k, v in candidates.items() if v.get("jurisdiction") != "us"}
    else:
        candidates = {k: v for k, v in candidates.items() if v.get("source") == "congress"}
    matched, _, _ = match(entries, [{"key": k} for k in sorted(candidates)], candidates)
    return {m.judgment["key"]: candidates[m.judgment["key"]] for m in matched}


def _evaluate_workdir(
    paths: Paths,
    work: Path,
    federal: bool,
    entries: list[dict[str, Any]],
    judge: Judge,
    settings: Settings,
    today: date,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, int]]:
    """(final judgments by key, candidate rows by key, counts)."""
    from . import federal as fed
    from . import prep

    prod = ClassifyPaths(paths, federal=federal)
    cp = ClassifyPaths(paths, work=work, federal=federal)
    vocab = Vocab.load(paths.vocab)
    rows = _items(cp, prod, entries)
    store = TextStore(cp.texts)
    counts = {"items": len(rows), "dropped": 0, "not_sent": 0}
    if not rows:
        return {}, rows, counts

    done = judged_keys(cp.out_dir("triage")) | assigned_keys(cp.stage_dir("triage"))
    items = [
        (fed.triage_item(r, store) if federal else prep.triage_item(r, store))
        for k, r in sorted(rows.items())
        if k not in done
    ]
    write_batches(cp, "triage", items, 150, PrepResult("triage"))
    t = judge_pending(
        cp, stage_spec(prod, "triage", vocab), judge, settings, today=today, prefix="e"
    )
    decisions = triage_decisions(cp)
    done = judged_keys(cp.out_dir("full")) | assigned_keys(cp.stage_dir("full"))
    bills = BillStore(cp.legiscan)
    items = []
    try:
        for k, d in sorted(decisions.items()):
            if d.get("decision") not in ("in", "needs_text") or k in done or k not in rows:
                continue
            view = {"decision": d["decision"], "reason": d.get("reason")}
            if federal:
                items.append(fed.full_item(rows[k], store, view, excerpt_cap=prep.EXCERPT_CAP))
            else:
                bill = bills.get(int(rows[k]["bill_id"]))
                items.append(prep.full_item(rows[k], bill, store, view))
    finally:
        bills.close()
    write_batches(cp, "full", items, 20, PrepResult("full"))
    f = judge_pending(cp, stage_spec(prod, "full", vocab), judge, settings, today=today, prefix="e")
    counts["dropped"] = len(t.dropped) + len(f.dropped)
    counts["not_sent"] = len(t.not_sent) + len(f.not_sent)

    final: dict[str, dict[str, Any]] = dict(decisions)
    for path in sorted(cp.out_dir("full").glob("*.jsonl")):
        for row in read_jsonl(path):
            final[row["key"]] = row
    return {k: v for k, v in final.items() if k in rows}, rows, counts


def run_eval(
    paths: Paths,
    judge: Judge,
    settings: Settings,
    gate: str,
    *,
    today: date,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    log = log or (lambda _m: None)
    entries = load_eval(eval_path(paths))
    judgments: dict[str, dict[str, Any]] = {}
    bills: dict[str, dict[str, Any]] = {}
    counts = {"items": 0, "dropped": 0, "not_sent": 0}
    for federal in (False, True):
        work = paths.cache / "classify" / "eval" / gate[:16] / ("federal" if federal else "state")
        j, rows, c = _evaluate_workdir(paths, work, federal, entries, judge, settings, today)
        log(f"eval {'federal' if federal else 'state'}: {c['items']} entries among candidates")
        judgments.update(j)
        bills.update(rows)
        for k in counts:
            counts[k] += c[k]
    result = score(entries, list(judgments.values()), bills=bills)
    overall = result.overall.as_dict()
    return {
        "model": settings.model,
        "effort": settings.effort,
        "date": today.isoformat(),
        "complete": counts["not_sent"] == 0,
        **counts,
        "matched": result.matched,
        "precision": overall["precision"],
        "recall": overall["recall"],
        "f1": overall["f1"],
        "tp": overall["tp"],
        "fp": overall["fp"],
        "fn": overall["fn"],
        "tn": overall["tn"],
        "false_positives": [r["id"] for r in result.false_positives],
        "false_negatives": [r["id"] for r in result.false_negatives],
    }


def judge_record(record: Mapping[str, Any], settings: Settings) -> list[str]:
    """Why a stored evaluation fails the floors (empty when it passes)."""
    reasons = []
    if not record.get("complete"):
        reasons.append("the evaluation did not finish within the spending cap")
    p, r = record.get("precision"), record.get("recall")
    if p is None or p < settings.precision_floor:
        reasons.append(f"precision {p} is below the floor {settings.precision_floor}")
    if r is None or r < settings.recall_floor:
        reasons.append(f"recall {r} is below the floor {settings.recall_floor}")
    return reasons


def eval_gate(
    paths: Paths,
    settings: Settings,
    judge_factory: Callable[[], Judge],
    *,
    today: date,
    force: bool = False,
    log: Callable[[str], None] | None = None,
) -> GateResult:
    gate = gate_hash(paths, settings)
    path = gate_path(paths)
    store = read_json(path) if path.exists() else {"results": {}}
    record = store["results"].get(gate)
    ran = False
    if record is None or force:
        record = run_eval(paths, judge_factory(), settings, gate, today=today, log=log)
        ran = True
        if record["complete"]:
            store["results"][gate] = record
            store["last"] = gate
            write_json(path, store)
    reasons = judge_record(record, settings)
    return GateResult(gate, dict(record), ran, not reasons, reasons)
