"""`edictai classify ...` subcommands."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from ..paths import Paths
from ..vocab import Vocab
from .common import (
    ClassifyPaths,
    expand,
    is_federal_file,
    latest_candidates,
    load_candidates,
    read_json,
    read_jsonl,
)
from .instructions import write_instructions
from .judgments import batch_for_output, check_full, check_triage, expected_keys, load_rows
from .prep import EXCERPT_CAP
from .texts import VERSIONS, TextStore


def _paths(args: argparse.Namespace) -> Paths:
    return Paths.discover(args.root)


def _is_federal(paths: Paths, args: argparse.Namespace) -> bool:
    """Federal when asked for, when --candidates is a federal file, or when every
    judgment file given lies in the federal working directory."""
    if getattr(args, "federal", False):
        return True
    if getattr(args, "candidates", None):
        return is_federal_file(Path(args.candidates))
    files = getattr(args, "files", None)
    if files:
        root = (paths.cache / "classify" / "federal").resolve()
        return all(Path(f).resolve().is_relative_to(root) for f in files)
    return False


def _cp(paths: Paths, args: argparse.Namespace) -> ClassifyPaths:
    work = getattr(args, "workdir", None)
    return ClassifyPaths(
        paths, Path(work).resolve() if work else None, federal=_is_federal(paths, args)
    )


def _rel(path: Path) -> str:
    rel = os.path.relpath(path)
    return str(path) if rel.startswith("..") else rel


def _keys(args: argparse.Namespace) -> list[str] | None:
    keys: list[str] = []
    for value in args.keys or []:
        p = Path(value)
        if p.is_file():
            if p.suffix == ".jsonl":
                keys += [r["key"] for r in read_jsonl(p)]
            else:
                keys += [
                    ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()
                ]
        else:
            keys += [k.strip() for k in value.split(",") if k.strip()]
    return keys or None


def _candidates(cp: ClassifyPaths, args: argparse.Namespace) -> dict:
    path = Path(args.candidates) if getattr(args, "candidates", None) else latest_candidates(cp)
    candidates = load_candidates(path)
    if cp.federal:
        from .federal import refresh_existing

        refresh_existing(candidates, cp.paths.data)
    return candidates


def cmd_prep(args: argparse.Namespace) -> int:
    from .prep import prep_full, prep_triage

    paths = _paths(args)
    cp = _cp(paths, args)
    written = write_instructions(paths, cp.root, federal=cp.federal)
    candidates = _candidates(cp, args)
    keys = _keys(args)
    if getattr(args, "rejudge", False) and keys is None:
        print("--rejudge needs --keys", file=sys.stderr)
        return 2
    args.batch_size = args.batch_size or default_batch_size(args.stage)
    if args.stage == "triage":
        result = prep_triage(
            cp, candidates, batch_size=args.batch_size, keys=keys, rebuild=args.rebuild
        )
    else:
        result = prep_full(
            cp,
            candidates,
            batch_size=args.batch_size,
            keys=keys,
            rebuild=args.rebuild,
            require_text=args.require_text,
            excerpt_cap=args.excerpt_cap,
            rejudge=args.rejudge,
        )
    noun = "items" if cp.federal else "bills"
    print(f"{result.items} {noun} in {len(result.batches)} new {args.stage} batches")
    for path in result.batches:
        print(f"  {_rel(path)}")
    if result.skipped_existing:
        where = "data/" if cp.federal else "data/legislation"
        print(f"Skipped {result.skipped_existing} already in {where}")
    if result.skipped_judged or result.skipped_assigned:
        print(
            f"Skipped {result.skipped_judged} already judged and "
            f"{result.skipped_assigned} already in a batch (use --rebuild to re-batch)"
        )
    if result.without_text:
        print(f"{len(result.without_text)} bills have no fetched text (run `classify fetch-texts`)")
    if result.unknown_keys:
        print(
            f"{len(result.unknown_keys)} keys are not in the candidate file: "
            + ", ".join(result.unknown_keys[:10])
        )
    print("Instructions: " + ", ".join(_rel(p) for p in written.values()))
    return 0


def cmd_instructions(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cp = _cp(paths, args)
    written = write_instructions(paths, cp.root, federal=cp.federal)
    for p in written.values():
        print(_rel(p))
    return 0


def cmd_fetch_texts(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    from ..ingest.legiscan import LegiScanClient, LegiScanError
    from .common import BillStore
    from .prep import triage_decisions
    from .texts import fetch_texts

    paths = _paths(args)
    cp = _cp(paths, args)
    if cp.federal:
        return _fetch_federal_texts(cp, args)
    candidates = _candidates(cp, args)
    keys = _keys(args)
    if keys is None:
        decisions = triage_decisions(cp)
        keys = [
            k for k, d in sorted(decisions.items()) if d.get("decision") in ("in", "needs_text")
        ]
    store = BillStore(cp.legiscan)
    pairs = []
    missing = []
    for key in keys:
        row = candidates.get(key)
        if row is None:
            missing.append(key)
            continue
        bill = store.get(int(row["bill_id"])) or row
        pairs.append((key, bill))
    store.close()
    load_dotenv(paths.root / ".env")
    try:
        client = LegiScanClient(os.environ.get("LEGISCAN_API_KEY", ""), cp.legiscan)
        result = fetch_texts(
            cp,
            client,
            pairs,
            max_queries=args.max_queries,
            refresh_extract=args.re_extract,
            version=args.version,
            progress=lambda m: print(m, file=sys.stderr),
        )
    except LegiScanError as exc:
        print(f"legiscan: {exc}", file=sys.stderr)
        return 1
    ok = len(result.fetched) + len(result.cached) - len(result.unavailable)
    print(
        f"{len(pairs)} bills: {len(result.fetched)} texts fetched, {len(result.cached)} cached; "
        f"{ok} with text, {len(result.unavailable) + len(result.no_text)} text_unavailable"
    )
    for key, why in result.unavailable[:20]:
        print(f"  unavailable {key}: {why}")
    if args.version != "latest":
        print(
            f"Earlier versions ({args.version}): {len(result.earlier)} stored, "
            f"{len(result.no_earlier)} without one"
        )
    if result.skipped_budget:
        print(f"{len(result.skipped_budget)} not fetched: --max-queries {args.max_queries} reached")
    for key, why in result.errors[:20]:
        print(f"  error {key}: {why}")
    if missing:
        print(f"{len(missing)} keys are not in the candidate file")
    print(f"Queries: {result.queries} this run, {result.month_total} this month")
    print(f"Texts: {_rel(cp.texts)}")
    return 1 if result.errors else 0


def _fetch_federal_texts(cp: ClassifyPaths, args: argparse.Namespace) -> int:
    """fetch-texts for federal candidates: Congress.gov and the Federal Register only."""
    from dotenv import load_dotenv

    from ..federal.congress import CongressClient, CongressError
    from .federal_texts import Fetcher, fetch_federal_texts
    from .prep import triage_decisions

    candidates = _candidates(cp, args)
    keys = _keys(args)
    if keys is None:
        decisions = triage_decisions(cp)
        keys = [
            k for k, d in sorted(decisions.items()) if d.get("decision") in ("in", "needs_text")
        ]
    pairs = [(k, candidates[k]) for k in keys if k in candidates]
    missing = [k for k in keys if k not in candidates]
    load_dotenv(cp.paths.root / ".env")
    congress = None
    if any(row.get("source") == "congress" for _, row in pairs):
        try:
            congress = CongressClient(os.environ.get("CONGRESS_API_KEY", ""), cp.congress)
        except CongressError as exc:
            print(f"congress.gov: {exc}", file=sys.stderr)
            return 1
    fetcher = Fetcher(cp, congress, max_queries=args.max_queries)
    result = fetch_federal_texts(
        cp,
        fetcher,
        pairs,
        refresh_extract=args.re_extract,
        version=args.version,
        progress=lambda m: print(m, file=sys.stderr),
    )
    ok = len(result.fetched) + len(result.cached) - len(result.unavailable)
    print(
        f"{len(pairs)} items: {len(result.fetched)} texts fetched, {len(result.cached)} cached; "
        f"{ok} with text, {len(result.unavailable) + len(result.no_text)} text_unavailable"
    )
    for key, why in result.unavailable[:20]:
        print(f"  unavailable {key}: {why}")
    for key in result.no_text[:20]:
        print(f"  no text listed {key}")
    if args.version != "latest":
        print(
            f"Earlier versions ({args.version}): {len(result.earlier)} stored, "
            f"{len(result.no_earlier)} without one"
        )
    if result.skipped_budget:
        print(f"{len(result.skipped_budget)} not fetched: --max-queries {args.max_queries} reached")
    for key, why in result.errors[:20]:
        print(f"  error {key}: {why}")
    if missing:
        print(f"{len(missing)} keys are not in the candidate file")
    print(f"Requests: {result.queries} this run")
    print(f"Texts: {_rel(cp.texts)}")
    return 1 if result.errors else 0


def _check(cp: ClassifyPaths, stage: str, files: list[Path], with_expected: bool):
    paths = cp.paths
    rows = load_rows(files)
    expected = None
    if with_expected and len(files) == 1:
        expected = expected_keys(batch_for_output(files[0]))
    if stage == "triage":
        return check_triage(rows, expected)
    return check_full(
        rows, Vocab.load(paths.vocab), TextStore(cp.texts), expected, federal=cp.federal
    )


def _detect_stage(files: list[Path]) -> str:
    rows = load_rows(files[:1])
    return "full" if rows and "in_scope" in rows[0] else "triage"


def cmd_check(args: argparse.Namespace) -> int:
    paths = _paths(args)
    files = expand(args.files)
    stage = args.stage or _detect_stage(files)
    report = _check(_cp(paths, args), stage, files, with_expected=True)
    for line in report.lines():
        print(line)
    print(report.summary(), file=sys.stderr)
    return 0 if report.ok else 1


def _openstates(paths: Paths):
    """Session dates from Open States: fetched (and cached) with a key, else the cache alone."""
    from dotenv import load_dotenv

    from ..ingest.sessions import CachedOpenStates, OpenStatesClient

    load_dotenv(paths.root / ".env")
    key = os.environ.get("OPENSTATES_API_KEY", "")
    cache = paths.cache / "openstates"
    return OpenStatesClient(key, cache) if key else CachedOpenStates(cache)


def cmd_import(args: argparse.Namespace) -> int:
    from .importer import import_judgments

    paths = _paths(args)
    cp = _cp(paths, args)
    target = Paths(Path(args.target).resolve()) if args.target else paths
    files = expand(args.files)
    report = _check(cp, "full", files, with_expected=False)
    candidates = _candidates(cp, args)
    if cp.federal:
        from .federal_import import import_federal

        result = import_federal(
            cp,
            target,
            report,
            candidates,
            today=date.today(),
            overwrite=args.overwrite,
            dry_run=args.dry_run,
            strict_quotes=args.strict_quotes,
        )
        return _import_report(args, target, result)
    sessions_path = cp.legiscan / "sessions.json"
    sessions = read_json(sessions_path) if sessions_path.exists() else {}
    result = import_judgments(
        cp,
        target,
        report,
        candidates,
        sessions,
        today=date.today(),
        openstates=_openstates(paths),
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        strict_quotes=args.strict_quotes,
    )
    return _import_report(args, target, result)


def _import_report(args: argparse.Namespace, target: Paths, result) -> int:
    from ..validate import validate

    verb = "Would write" if args.dry_run else "Wrote"
    print(
        f"{verb} {len(result.written)} records ({result.machine_verified} machine-verified, "
        f"{result.unverified} unverified) to {_rel(target.data)}; "
        f"{result.out_of_scope} judged out of scope"
    )
    for p in result.written:
        print(f"  {_rel(p)}")
    if result.dropped_provisions:
        print(f"{result.dropped_provisions} provisions dropped for unverified quotes")
    for key, errors in result.rejected:
        print(f"Rejected {key}: " + "; ".join(errors))
    for key, why in result.skipped:
        print(f"Skipped {key}: {why}")
    for jur, ident in result.sessions_added:
        print(f"Added session {ident} to data/jurisdictions/{jur}.yaml")
    if args.dry_run:
        return 0
    vreport, _ = validate(target, strict=True)
    written = {os.path.realpath(p) for p in result.written}
    relevant = [
        i
        for i in vreport.issues
        if os.path.realpath(target.root / i.file) in written or "jurisdictions/" in i.file
    ]
    for issue in relevant:
        print(issue.format())
    print(f"validate --strict: {vreport.summary()}")
    return 0 if vreport.ok and not result.rejected else 1


def cmd_score(args: argparse.Namespace) -> int:
    from .score import format_report, load_eval, score

    paths = _paths(args)
    cp = _cp(paths, args)
    entries = load_eval(
        Path(args.eval) if args.eval else paths.root / "pipeline" / "eval" / "classifier_eval.yaml"
    )
    rows = load_rows(expand(args.files))
    # Later lines win when a key was judged more than once.
    judgments = list({r["key"]: r for r in rows if isinstance(r.get("key"), str)}.values())
    try:
        bills = _candidates(cp, args)
    except FileNotFoundError:
        bills = {}
    result = score(
        entries,
        judgments,
        needs_text_as_in=not args.needs_text_as_out,
        missing_as_out=args.missing_as_out,
        bills=bills,
    )
    if args.format == "json":
        print(json.dumps(result.as_dict(), indent=1))
    else:
        print(format_report(result))
    return 0


# -- automated run ------------------------------------------------------------------


def _settings(paths: Paths, args: argparse.Namespace):
    from dotenv import load_dotenv

    from .settings import load_settings

    load_dotenv(paths.root / ".env")
    settings = load_settings(paths)
    return settings.with_overrides(
        model=getattr(args, "model", None),
        effort=getattr(args, "effort", None),
        spend_cap_usd=getattr(args, "spend_cap", None),
        use_batches=False if getattr(args, "no_batches", False) else None,
    )


def _judge(settings, args: argparse.Namespace):
    from .llm import Judge, make_budget

    ledger = Path(args.spend_file).resolve() if getattr(args, "spend_file", None) else None
    budget = make_budget(settings, ledger)
    return Judge(settings, budget, log=lambda m: print(m, file=sys.stderr, flush=True))


def _workdirs(paths: Paths, args: argparse.Namespace) -> list[ClassifyPaths]:
    work = Path(args.workdir).resolve() if getattr(args, "workdir", None) else None
    if getattr(args, "federal", False) or args.scope == "federal":
        return [ClassifyPaths(paths, work, federal=True)]
    if args.scope == "state":
        return [ClassifyPaths(paths, work)]
    if work:
        raise SystemExit("--workdir needs --scope state or federal")
    return [ClassifyPaths(paths), ClassifyPaths(paths, federal=True)]


def cmd_run(args: argparse.Namespace) -> int:
    import anthropic

    from .llm import ClaudeCodeError, spend_line
    from .run import run_workdir
    from .settings import SettingsError

    paths = _paths(args)
    try:
        settings = _settings(paths, args)
        judge = _judge(settings, args)
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return 2
    output: dict = {"model": settings.model, "effort": settings.effort, "judgments": []}
    code = 0
    for cp in _workdirs(paths, args):
        name = "federal" if cp.federal else "state"
        try:
            res = run_workdir(
                cp,
                judge,
                settings,
                today=date.today(),
                candidates_path=Path(args.candidates) if args.candidates else None,
                retry_dropped=args.retry_dropped,
                limit=args.limit,
                fetch=not args.no_fetch,
                log=lambda m: print(m, file=sys.stderr, flush=True),
            )
        except FileNotFoundError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            continue
        except anthropic.AnthropicError as exc:
            print(f"{name}: Anthropic API: {type(exc).__name__}: {exc}", file=sys.stderr)
            code = 1
            break
        except ClaudeCodeError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            code = 1
            break
        output[name] = res.as_dict()
        output["judgments"] += res.judgments()
        t, f = res.triage, res.full
        print(
            f"{name}: triage {len(t.judged)} judged, {len(t.dropped)} dropped, "
            f"{len(t.not_sent)} not sent; full {len(f.judged)} judged, {len(f.dropped)} "
            f"dropped, {len(f.not_sent)} not sent; texts {res.texts.fetched} fetched"
            + (f" ({res.texts.skipped})" if res.texts.skipped else "")
        )
        for out in f.outputs:
            print(f"  {_rel(out)}")
    output["spend"] = {**judge.budget.as_dict(), "model": settings.model, "effort": settings.effort}
    output["backend"] = settings.backend
    output["batches"] = judge.batches
    print(spend_line(judge.budget.as_dict()) + (" (cap reached)" if judge.budget.stopped else ""))
    if args.result:
        Path(args.result).write_text(json.dumps(output, indent=1) + "\n", encoding="utf-8")
    return code


def cmd_baseline(args: argparse.Namespace) -> int:
    from .prep import judged_keys
    from .run import unjudged, write_baseline

    paths = _paths(args)
    for cp in _workdirs(paths, args):
        name = "federal" if cp.federal else "state"
        if args.if_new and (
            (cp.root / "baseline.json").exists() or judged_keys(cp.out_dir("triage"))
        ):
            print(f"{name}: judgments or a baseline exist; nothing recorded")
            continue
        try:
            candidates = load_candidates(latest_candidates(cp))
        except FileNotFoundError:
            print(f"{name}: no candidate file; nothing recorded")
            continue
        if cp.federal:
            from .federal import refresh_existing

            refresh_existing(candidates, paths.data)
        added = write_baseline(cp, unjudged(cp, candidates))
        print(f"{name}: {added} candidates recorded in {_rel(cp.root / 'baseline.json')}")
    return 0


def cmd_eval_gate(args: argparse.Namespace) -> int:
    import anthropic
    from dotenv import load_dotenv

    from .evalgate import eval_gate
    from .llm import ClaudeCodeError
    from .settings import SettingsError

    paths = _paths(args)
    # LegiScan and Congress.gov keys, for texts the evaluation has to fetch.
    load_dotenv(paths.root / ".env")
    try:
        settings = _settings(paths, args)
        settings.price()
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        result = eval_gate(
            paths,
            settings,
            lambda: _judge(settings, args),
            today=date.today(),
            force=args.force,
            log=lambda m: print(m, file=sys.stderr, flush=True),
        )
    except anthropic.AnthropicError as exc:
        print(f"Anthropic API: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except ClaudeCodeError as exc:
        print(exc, file=sys.stderr)
        return 1
    r = result.record
    how = "evaluated now" if result.ran else "stored result"
    print(
        f"Eval gate {result.hash[:12]} ({how}): precision {r.get('precision')}, recall "
        f"{r.get('recall')} on {r.get('matched')} entries; floors "
        f"{settings.precision_floor}/{settings.recall_floor}: "
        + ("passed" if result.passed else "FAILED")
    )
    for reason in result.reasons:
        print(f"  {reason}")
    if args.json:
        Path(args.json).write_text(json.dumps(result.as_dict(), indent=1) + "\n", encoding="utf-8")
    return 0 if result.passed else 1


def cmd_refresh(args: argparse.Namespace) -> int:
    from .refresh import refresh

    paths = _paths(args)
    sources = args.source or ["legiscan", "congress"]
    result = refresh(paths, today=date.today(), sources=sources, dry_run=args.dry_run)
    verb = "Would update" if args.dry_run else "Updated"
    for u in result.updates:
        change = (
            f"{u.status_before} -> {u.status_after}"
            if u.status_before != u.status_after
            else u.status_after
        )
        print(
            f"{verb} {u.id}: {len(u.added_actions)} actions, {len(u.added_versions)} versions "
            f"({change})"
        )
    for rid, why in result.skipped:
        print(f"Skipped {rid}: {why}")
    print(f"{len(result.updates)} of {result.checked} bills updated")
    if args.json:
        Path(args.json).write_text(
            json.dumps(result.as_dict(paths.root), indent=1) + "\n", encoding="utf-8"
        )
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    from .summary import build_summary, markdown

    paths = _paths(args)
    summary = build_summary(
        paths,
        run=Path(args.run) if args.run else None,
        refresh=Path(args.refresh) if args.refresh else None,
        gate=Path(args.gate) if args.gate else None,
        article=Path(args.article) if args.article else None,
        status=args.status,
        failed_step=args.failed_step,
    )
    body = markdown(summary)
    if args.markdown:
        Path(args.markdown).write_text(body, encoding="utf-8")
    else:
        print(body)
    if args.json:
        Path(args.json).write_text(json.dumps(summary.as_dict(), indent=1) + "\n", encoding="utf-8")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    c = sub.add_parser("classify", help="prepare, judge, check, import, and score scope judgments")
    c.add_argument(
        "--workdir",
        help="directory for batches, judgments, and instructions (default: .cache/classify)",
    )
    c.add_argument(
        "--federal",
        action="store_true",
        help="federal candidates (Congress.gov, Federal Register), with their own working "
        "directory (.cache/classify/federal) and texts; implied by a federal --candidates file",
    )
    csub = c.add_subparsers(dest="classify_command", required=True)

    def candidates_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument("--candidates", help="candidate file (default: newest in .cache/candidates)")

    def keys_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--keys",
            action="append",
            help="limit to these bill keys: comma-separated, or a file of keys or JSON lines",
        )

    p = csub.add_parser("prep", help="write batch files and reviewer instructions")
    p.add_argument("--stage", choices=("triage", "full"), required=True)
    p.add_argument("--batch-size", type=int, default=None)
    p.add_argument("--rebuild", action="store_true", help="discard unjudged batches and re-batch")
    p.add_argument(
        "--require-text",
        action="store_true",
        help="full stage: leave out bills with no fetched text",
    )
    p.add_argument(
        "--excerpt-cap",
        type=int,
        default=EXCERPT_CAP,
        help=f"full stage: characters of excerpts per text version (default {EXCERPT_CAP})",
    )
    p.add_argument(
        "--rejudge",
        action="store_true",
        help="full stage, with --keys: batch these bills again even if already judged or batched",
    )
    candidates_arg(p)
    keys_arg(p)
    p.set_defaults(func=cmd_prep)

    p = csub.add_parser("instructions", help="regenerate TRIAGE.md, FULL.md, and output schemas")
    p.set_defaults(func=cmd_instructions)

    p = csub.add_parser(
        "fetch-texts", help="fetch the latest text of bills triaged in or needs_text (getBillText)"
    )
    p.add_argument(
        "--max-queries", type=int, default=500, help="API queries this run (default 500)"
    )
    p.add_argument(
        "--re-extract", action="store_true", help="re-extract text from cached responses"
    )
    p.add_argument(
        "--version",
        choices=VERSIONS,
        default="latest",
        help="also store an earlier version beside the latest: the earliest with text, "
        "or (ai) the newest earlier version whose text mentions AI",
    )
    candidates_arg(p)
    keys_arg(p)
    p.set_defaults(func=cmd_fetch_texts)

    p = csub.add_parser("check", help="check a judgment file (exit 1 on any error)")
    p.add_argument("files", nargs="+")
    p.add_argument("--stage", choices=("triage", "full"))
    p.set_defaults(func=cmd_check)

    p = csub.add_parser("import", help="write records for accepted in-scope judgments")
    p.add_argument("files", nargs="+", help="full-stage judgment files or directories")
    p.add_argument("--target", help="repository root to write into (default: this one)")
    p.add_argument("--overwrite", action="store_true", help="replace existing record files")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--strict-quotes",
        action="store_true",
        help="reject a judgment with any unverified quote instead of dropping that provision",
    )
    candidates_arg(p)
    p.set_defaults(func=cmd_import)

    p = csub.add_parser("score", help="precision and recall against the evaluation set")
    p.add_argument("files", nargs="+", help="judgment files (full or triage) or directories")
    p.add_argument("--eval", help="evaluation file (default: pipeline/eval/classifier_eval.yaml)")
    p.add_argument(
        "--needs-text-as-out", action="store_true", help="triage files: count needs_text as out"
    )
    p.add_argument(
        "--missing-as-out",
        action="store_true",
        help="count evaluation entries with no judgment as judged out",
    )
    p.add_argument("--format", choices=("text", "json"), default="text")
    candidates_arg(p)
    p.set_defaults(func=cmd_score)

    def scope_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--scope",
            choices=("both", "state", "federal"),
            default="both",
            help="working directories to use (default both; --federal means federal)",
        )

    def api_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--model", help="model id (default: pipeline/automation.yaml)")
        p.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"))
        p.add_argument(
            "--spend-cap",
            type=float,
            help="dollar cap for this run (default: WEEKLY_SPEND_CAP_USD, else the settings file)",
        )
        p.add_argument(
            "--spend-file",
            help="JSON ledger of dollars spent, shared by the commands of one run",
        )
        p.add_argument(
            "--no-batches", action="store_true", help="direct calls instead of Message Batches"
        )

    p = csub.add_parser("run", help="judge new candidates with Claude (triage, texts, full)")
    scope_arg(p)
    api_args(p)
    p.add_argument("--result", help="write the run's result as JSON to this path")
    p.add_argument("--limit", type=int, help="at most this many new items per stage")
    p.add_argument(
        "--retry-dropped", action="store_true", help="judge items dropped by earlier runs again"
    )
    p.add_argument("--no-fetch", action="store_true", help="do not fetch missing bill texts")
    candidates_arg(p)
    p.set_defaults(func=cmd_run)

    p = csub.add_parser(
        "baseline",
        help="record the unjudged candidates of the newest candidate files so `run` skips them",
    )
    scope_arg(p)
    p.add_argument(
        "--if-new",
        action="store_true",
        help="only when the working directory has no judgments and no baseline yet",
    )
    p.set_defaults(func=cmd_baseline)

    p = csub.add_parser(
        "eval-gate",
        help="score the automated classifier on the evaluation set when its instructions, "
        "model, or effort changed; exit 1 below the floors",
    )
    api_args(p)
    p.add_argument("--force", action="store_true", help="evaluate even if a result is stored")
    p.add_argument("--json", help="write the gate result as JSON to this path")
    p.set_defaults(func=cmd_eval_gate)

    p = csub.add_parser(
        "refresh", help="append newly recorded actions and versions to existing bill records"
    )
    p.add_argument(
        "--source",
        action="append",
        choices=("legiscan", "congress"),
        help="limit to one source (repeatable)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", help="write the updates as JSON to this path")
    p.set_defaults(func=cmd_refresh)

    p = csub.add_parser("summary", help="write the weekly run's summary and result file")
    p.add_argument("--run", help="`classify run --result` file")
    p.add_argument("--refresh", help="`classify refresh --json` file")
    p.add_argument("--gate", help="`classify eval-gate --json` file")
    p.add_argument("--article", help="`articles write --result` file")
    p.add_argument("--status", choices=("success", "failure"), default="success")
    p.add_argument("--failed-step", help="the step that failed, for a failure summary")
    p.add_argument("--markdown", help="write the summary here (default: stdout)")
    p.add_argument("--json", help="write the machine-readable result here")
    p.set_defaults(func=cmd_summary)


def default_batch_size(stage: str) -> int:
    return 150 if stage == "triage" else 20
