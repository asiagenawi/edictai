"""`classify run`: triage and full judgments from the Anthropic API.

For one working directory (state, or federal with `--federal`):

1. The instructions and output schemas are regenerated (`write_instructions`).
2. Candidates nobody has judged are batched for triage (`prep --stage triage`),
   leaving out keys in `baseline.json` (candidates that existed before the
   automated run took over) and `dropped.json` (judgments that failed twice).
3. Every triage batch without an output file is judged.
4. Bills triaged `in` or `needs_text` get their text (`fetch-texts`, within the
   LegiScan or federal request budget) and are batched for the full stage.
5. Every full batch without an output file is judged.

Each request's system prompt is the stage's instruction file (TRIAGE.md or
FULL.md) and its structured output is the stage's judgment schema. Judgments
go through the same checks as `classify check`; items with errors are asked
once more with the errors listed, and items that fail again are dropped and
recorded in `dropped.json`. Judgments that pass are written to
`{stage}/out/batch-NNN.jsonl`, and the batch file is rewritten to the items
judged, so `classify check` and `classify import` work on the outputs as on
hand-written ones. Items the budget did not cover are taken out of their batch
and are batched again by the next run.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..vocab import Vocab
from .common import (
    BillStore,
    ClassifyPaths,
    latest_candidates,
    load_candidates,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)
from .instructions import write_instructions
from .judgments import CheckReport, check_full, check_triage, expected_keys
from .llm import Judge, Request, api_schema
from .prep import (
    assigned_keys,
    judged_keys,
    prep_full,
    prep_triage,
    triage_decisions,
    write_manifest,
)
from .settings import Settings
from .texts import TextStore

TRIAGE_BATCH = 150
FULL_BATCH = 20

PROMPT = """\
Judge each item below by the instructions. The items are the lines of one batch file, \
in order. Reply with {{"judgments": [...]}}: one judgment object per item, in the same \
order, each key exactly once, following the output rules and the schema in the \
instructions. You cannot run commands; the checks the instructions describe are run on \
your reply.

Items ({count}, one JSON object per line):

{items}
"""

RETRY = """

Your previous judgment of this item failed these checks:
{errors}

Previous judgment:
{previous}

Reply with a corrected judgment that passes them."""


def prompt_templates() -> str:
    """The text wrapped around the items, which the evaluation hash covers."""
    return PROMPT + RETRY


# -- ledgers ----------------------------------------------------------------------


def _ledger_path(cp: ClassifyPaths, name: str) -> Path:
    return cp.root / name


def baseline_keys(cp: ClassifyPaths) -> set[str]:
    path = _ledger_path(cp, "baseline.json")
    return set(read_json(path)) if path.exists() else set()


def write_baseline(cp: ClassifyPaths, keys: Iterable[str]) -> int:
    """Add keys to baseline.json; returns how many were new."""
    have = baseline_keys(cp)
    new = set(keys) - have
    write_json(_ledger_path(cp, "baseline.json"), sorted(have | new))
    return len(new)


def dropped(cp: ClassifyPaths) -> dict[str, dict[str, Any]]:
    path = _ledger_path(cp, "dropped.json")
    return read_json(path) if path.exists() else {}


def _record_dropped(cp: ClassifyPaths, rows: Mapping[str, Mapping[str, Any]]) -> None:
    if not rows:
        return
    data = dropped(cp)
    data.update({k: dict(v) for k, v in rows.items()})
    write_json(_ledger_path(cp, "dropped.json"), dict(sorted(data.items())))


def _clear_dropped(cp: ClassifyPaths, stage: str) -> None:
    data = dropped(cp)
    kept = {k: v for k, v in data.items() if v.get("stage") != stage}
    if kept != data:
        write_json(_ledger_path(cp, "dropped.json"), kept)


# -- judging ------------------------------------------------------------------------


@dataclass
class StageResult:
    stage: str
    judged: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)  # {key, errors}
    not_sent: list[str] = field(default_factory=list)  # back to the pool (budget)
    retried: int = 0
    outputs: list[Path] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "judged": len(self.judged),
            "dropped": self.dropped,
            "not_sent": self.not_sent,
            "retried": self.retried,
            "outputs": [str(p) for p in self.outputs],
        }


@dataclass
class StageSpec:
    stage: str
    system: str
    schema: dict[str, Any]
    # (rows, expected keys in order or None) -> the report `classify check` gives
    check: Callable[[list[dict[str, Any]], list[str] | None], CheckReport]


def stage_spec(cp: ClassifyPaths, stage: str, vocab: Vocab) -> StageSpec:
    """The instructions and schema written for `cp`, and the matching check."""
    system = cp.instructions(stage).read_text(encoding="utf-8")
    schema = api_schema(read_json(cp.output_schema(stage)))
    if stage == "triage":
        return StageSpec(stage, system, schema, lambda rows, exp=None: check_triage(rows, exp))
    store = TextStore(cp.texts)
    return StageSpec(
        stage,
        system,
        schema,
        lambda rows, exp=None: check_full(rows, vocab, store, exp, federal=cp.federal),
    )


def _errors(report: CheckReport) -> dict[str, list[str]]:
    return {c.key: list(c.errors) for c in report.items if c.errors or c.quote_failures}


def _collect(
    reqs: list[Request], keys: Mapping[str, list[str]], replies: Mapping[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], list[str]]:
    """(rows by key, errors by key for items with no usable row, keys never sent)."""
    rows: dict[str, dict[str, Any]] = {}
    errors: dict[str, list[str]] = {}
    unsent: list[str] = []
    for req in reqs:
        reply = replies[req.id]
        wanted = keys[req.id]
        if not reply.sent and reply.error and "spending cap" in reply.error:
            unsent += wanted
            continue
        found = (reply.data or {}).get("judgments") if isinstance(reply.data, dict) else None
        for row in found or []:
            k = row.get("key") if isinstance(row, dict) else None
            if k in wanted and k not in rows:
                rows[k] = row
        for k in wanted:
            if k not in rows:
                errors[k] = [reply.error or "no judgment returned for this item"]
    return rows, errors, unsent


def judge_items(
    judge: Judge,
    spec: StageSpec,
    items: list[dict[str, Any]],
    settings: Settings,
    *,
    prefix: str = "",
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], list[str], int]:
    """Judge items: (passing rows by key, errors of dropped keys, keys not sent, retried)."""
    size = max(1, int(settings.items_per_request.get(spec.stage, 1)))
    max_tokens = int(settings.max_tokens.get(spec.stage, 8000))
    tag = f"{prefix}{spec.stage[0]}"
    reqs: list[Request] = []
    keys: dict[str, list[str]] = {}
    for n, i in enumerate(range(0, len(items), size), 1):
        chunk = items[i : i + size]
        rid = f"{tag}-{n:05d}"
        body = "\n".join(json.dumps(x, ensure_ascii=False) for x in chunk)
        reqs.append(
            Request(
                rid,
                spec.system,
                PROMPT.format(count=len(chunk), items=body),
                spec.schema,
                max_tokens,
            )
        )
        keys[rid] = [x["key"] for x in chunk]
    rows, errors, unsent = _collect(reqs, keys, judge.run(reqs))
    report = spec.check(list(rows.values()), None)
    for k, errs in _errors(report).items():
        errors[k] = errs
    passing = {k: r for k, r in rows.items() if k not in errors}

    # One more try for each item with errors, alone, with the errors listed.
    by_key = {x["key"]: x for x in items}
    retry_reqs: list[Request] = []
    retry_keys: dict[str, list[str]] = {}
    for n, (k, errs) in enumerate(sorted(errors.items()), 1):
        rid = f"{tag}r-{n:05d}"
        previous = json.dumps(rows[k], ensure_ascii=False) if k in rows else "(none)"
        user = PROMPT.format(count=1, items=json.dumps(by_key[k], ensure_ascii=False))
        user += RETRY.format(errors="\n".join(f"- {e}" for e in errs), previous=previous)
        retry_reqs.append(Request(rid, spec.system, user, spec.schema, max_tokens))
        retry_keys[rid] = [k]
    failed: dict[str, list[str]] = {}
    if retry_reqs:
        rows2, errors2, unsent2 = _collect(retry_reqs, retry_keys, judge.run(retry_reqs))
        report2 = spec.check(list(rows2.values()), None)
        errors2.update(_errors(report2))
        unsent += unsent2
        for k in errors:
            if k in unsent2:
                continue
            if k in rows2 and k not in errors2:
                passing[k] = rows2[k]
            else:
                failed[k] = errors2.get(k) or errors[k]
    return passing, failed, unsent, len(retry_reqs)


def pending_batches(cp: ClassifyPaths, stage: str) -> list[Path]:
    stage_dir = cp.stage_dir(stage)
    return [
        p
        for p in sorted(stage_dir.glob("batch-*.jsonl"))
        if not (cp.out_dir(stage) / p.name).exists()
    ]


def judge_pending(
    cp: ClassifyPaths,
    spec: StageSpec,
    judge: Judge,
    settings: Settings,
    *,
    today: date,
    prefix: str = "",
) -> StageResult:
    """Judge every batch of the stage that has no output file yet."""
    stage = spec.stage
    result = StageResult(stage)
    batches = pending_batches(cp, stage)
    if not batches:
        return result
    lines = {b: read_jsonl(b) for b in batches}
    items = [row for b in batches for row in lines[b]]
    passing, failed, unsent, retried = judge_items(judge, spec, items, settings, prefix=prefix)
    result.retried = retried
    result.not_sent = sorted(unsent)
    result.dropped = [{"key": k, "errors": errs} for k, errs in sorted(failed.items())]
    _record_dropped(
        cp,
        {k: {"stage": stage, "date": today.isoformat(), "errors": e} for k, e in failed.items()},
    )
    out_dir = cp.out_dir(stage)
    out_dir.mkdir(parents=True, exist_ok=True)
    for batch in batches:
        kept = [row for row in lines[batch] if row["key"] in passing]
        if not kept:
            batch.unlink()
            continue
        write_jsonl(batch, kept)
        out = out_dir / batch.name
        write_jsonl(out, [passing[row["key"]] for row in kept])
        # The same check `classify check` runs on an output file.
        report = spec.check(read_jsonl(out), expected_keys(batch))
        if not report.ok:
            raise RuntimeError(f"{out} fails its check: {'; '.join(report.lines()[:5])}")
        result.outputs.append(out)
        result.judged += [passing[row["key"]] for row in kept]
    write_manifest(cp, stage)
    return result


# -- texts ------------------------------------------------------------------------


@dataclass
class TextsResult:
    requested: int = 0
    fetched: int = 0
    queries: int = 0
    skipped: str | None = None  # why no text was fetched
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def fetch_missing_texts(
    cp: ClassifyPaths,
    candidates: Mapping[str, Mapping[str, Any]],
    keys: list[str],
    settings: Settings,
    env: Mapping[str, str],
    *,
    log: Callable[[str], None],
) -> TextsResult:
    """Fetch the text of bills that have none stored, within the request budgets."""
    store = TextStore(cp.texts)
    need = [k for k in keys if store.for_key(k)[0] is None and k in candidates]
    result = TextsResult(requested=len(need))
    if not need:
        return result
    if cp.federal:
        from ..federal.congress import CongressClient, CongressError
        from .federal_texts import Fetcher, fetch_federal_texts

        congress = None
        if any(candidates[k].get("source") == "congress" for k in need):
            try:
                congress = CongressClient(env.get("CONGRESS_API_KEY", ""), cp.congress)
            except CongressError as exc:
                result.errors.append(f"congress.gov: {exc}")
        fetcher = Fetcher(cp, congress, max_queries=settings.federal_text_requests)
        got = fetch_federal_texts(
            cp,
            fetcher,
            [(k, candidates[k]) for k in need],
            version=settings.text_version,
            progress=log,
        )
    else:
        from ..ingest.legiscan import MONTHLY_LIMIT, LegiScanClient, LegiScanError
        from .texts import fetch_texts

        try:
            client = LegiScanClient(env.get("LEGISCAN_API_KEY", ""), cp.legiscan)
        except LegiScanError as exc:
            result.skipped = str(exc)
            return result
        allowed = min(
            settings.legiscan_text_queries,
            MONTHLY_LIMIT - settings.legiscan_monthly_reserve - client.usage.used(),
        )
        if allowed <= 0:
            result.skipped = (
                f"LegiScan monthly budget: {client.usage.used()} of {MONTHLY_LIMIT} used, "
                f"{settings.legiscan_monthly_reserve} reserved"
            )
            return result
        bills = BillStore(cp.legiscan)
        try:
            pairs = [(k, bills.get(int(candidates[k]["bill_id"])) or candidates[k]) for k in need]
        finally:
            bills.close()
        try:
            got = fetch_texts(
                cp, client, pairs, max_queries=allowed, version=settings.text_version, progress=log
            )
        except LegiScanError as exc:
            result.errors.append(f"legiscan: {exc}")
            return result
    result.fetched = len(got.fetched)
    result.queries = got.queries
    result.errors += [f"{k}: {why}" for k, why in got.errors]
    if got.skipped_budget:
        result.errors.append(f"{len(got.skipped_budget)} texts not fetched: request budget reached")
    return result


# -- one working directory -----------------------------------------------------------


@dataclass
class WorkdirResult:
    federal: bool
    candidates: Path | None = None
    triage: StageResult = field(default_factory=lambda: StageResult("triage"))
    full: StageResult = field(default_factory=lambda: StageResult("full"))
    texts: TextsResult = field(default_factory=TextsResult)
    batched: dict[str, int] = field(default_factory=dict)
    # title and url of every judged or dropped item, for the summary
    info: dict[str, dict[str, Any]] = field(default_factory=dict)
    # every full-stage output `run` has written in this working directory
    import_files: list[Path] = field(default_factory=list)

    def judgments(self) -> list[dict[str, Any]]:
        out = []
        workdir = "federal" if self.federal else "state"
        for stage in (self.triage, self.full):
            for row in stage.judged:
                out.append(
                    {
                        "key": row["key"],
                        "stage": stage.stage,
                        "workdir": workdir,
                        **{k: row.get(k) for k in ("decision", "reason") if k in row},
                        **{
                            k: row.get(k)
                            for k in ("in_scope", "confidence", "reasons")
                            if stage.stage == "full"
                        },
                        **self.info.get(row["key"], {}),
                    }
                )
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "federal": self.federal,
            "candidates": str(self.candidates) if self.candidates else None,
            "batched": self.batched,
            "triage": self.triage.as_dict(),
            "texts": self.texts.as_dict(),
            "full": self.full.as_dict(),
            "full_outputs": [str(p) for p in self.full.outputs],
            "import_files": [str(p) for p in self.import_files],
        }


def candidate_info(row: Mapping[str, Any]) -> dict[str, Any]:
    """Title and official link of a candidate row."""
    if row.get("source") == "federal_register":
        url = row.get("html_url")
    else:
        url = row.get("url") or row.get("state_link")
    if url and "legiscan.com" in str(url).lower():
        url = None
    return {"title": row.get("title"), "url": url}


def _last_activity(row: Mapping[str, Any]) -> str:
    dates = [str(h.get("date") or "") for h in row.get("history") or []]
    for k in ("status_date", "publication_date", "update_date", "introduced_date"):
        dates.append(str(row.get(k) or ""))
    if isinstance(row.get("latest_action"), Mapping):
        dates.append(str(row["latest_action"].get("date") or ""))
    return max(dates)


def unjudged(cp: ClassifyPaths, candidates: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Candidate keys with no triage judgment or batch, not recorded, not baselined, and
    not dropped; the most recently active first."""
    skip = (
        judged_keys(cp.out_dir("triage"))
        | assigned_keys(cp.stage_dir("triage"))
        | baseline_keys(cp)
        | {k for k, v in dropped(cp).items() if v.get("stage") == "triage"}
    )
    keys = [k for k, r in candidates.items() if not r.get("existing_record") and k not in skip]
    return sorted(keys, key=lambda k: (_last_activity(candidates[k]), k), reverse=True)


def full_keys(cp: ClassifyPaths, candidates: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Keys triaged in or needs_text that have no full judgment or batch yet."""
    skip = (
        judged_keys(cp.out_dir("full"))
        | assigned_keys(cp.stage_dir("full"))
        | {k for k, v in dropped(cp).items() if v.get("stage") == "full"}
    )
    out = []
    for k, d in sorted(triage_decisions(cp).items()):
        row = candidates.get(k)
        if d.get("decision") not in ("in", "needs_text") or k in skip or row is None:
            continue
        if row.get("existing_record"):
            continue
        out.append(k)
    return out


def run_workdir(
    cp: ClassifyPaths,
    judge: Judge,
    settings: Settings,
    *,
    today: date,
    candidates_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    retry_dropped: bool = False,
    limit: int | None = None,
    fetch: bool = True,
    log: Callable[[str], None] | None = None,
) -> WorkdirResult:
    log = log or (lambda _m: None)
    env = os.environ if env is None else env
    result = WorkdirResult(cp.federal)
    write_instructions(cp.paths, cp.root, federal=cp.federal)
    vocab = Vocab.load(cp.paths.vocab)
    path = candidates_path or latest_candidates(cp)
    result.candidates = path
    candidates = load_candidates(path)
    if cp.federal:
        from .federal import refresh_existing

        refresh_existing(candidates, cp.paths.data)
    if retry_dropped:
        for stage in ("triage", "full"):
            _clear_dropped(cp, stage)

    keys = unjudged(cp, candidates)
    if limit is not None:
        keys = keys[:limit]
    prepped = prep_triage(cp, candidates, batch_size=TRIAGE_BATCH, keys=keys)
    result.batched["triage"] = prepped.items
    log(f"triage: {prepped.items} new items; {len(pending_batches(cp, 'triage'))} batches to judge")
    result.triage = judge_pending(cp, stage_spec(cp, "triage", vocab), judge, settings, today=today)

    keys = full_keys(cp, candidates)
    if limit is not None:
        keys = keys[:limit]
    if fetch and keys:
        result.texts = fetch_missing_texts(cp, candidates, keys, settings, env, log=log)
    prepped = prep_full(cp, candidates, batch_size=FULL_BATCH, keys=keys)
    result.batched["full"] = prepped.items
    log(f"full: {prepped.items} new items; {len(pending_batches(cp, 'full'))} batches to judge")
    result.full = judge_pending(cp, stage_spec(cp, "full", vocab), judge, settings, today=today)
    for stage in (result.triage, result.full):
        for key in [r["key"] for r in stage.judged] + [d["key"] for d in stage.dropped]:
            if key in candidates:
                result.info[key] = candidate_info(candidates[key])
    result.import_files = record_outputs(cp, result.full.outputs)
    return result


def record_outputs(cp: ClassifyPaths, outputs: Iterable[Path]) -> list[Path]:
    """Add full-stage outputs to `auto-outputs.json`, the list of every output `run` has
    written; returns the listed files that still exist. Importing all of them each time
    keeps a record from a pull request that was not merged in the next week's changes
    (records already in data/ are skipped by `import`)."""
    path = _ledger_path(cp, "auto-outputs.json")
    listed = read_json(path) if path.exists() else []
    for out in outputs:
        rel = os.path.relpath(out, cp.root)
        if rel not in listed:
            listed.append(rel)
    write_json(path, listed)
    return [cp.root / rel for rel in listed if (cp.root / rel).exists()]
