"""Command-line entry point: `edictai validate | build | docs | ingest | watch | watch-apply |
classify | articles`."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from . import docs
from .build import BuildError, build
from .paths import Paths
from .validate import validate


def _paths(args: argparse.Namespace) -> Paths:
    return Paths.discover(args.root)


def cmd_validate(args: argparse.Namespace) -> int:
    report, _ = validate(_paths(args), strict=args.strict)
    if args.format == "json":
        print(report.to_json())
    else:
        github = args.format == "github"
        for issue in report.issues:
            print(issue.github() if github else issue.format())
        print(report.summary(), file=sys.stderr)
    return 0 if report.ok else 1


def cmd_build(args: argparse.Namespace) -> int:
    paths = _paths(args)
    out = Path(args.out).resolve() if args.out else None
    try:
        result = build(
            paths,
            out,
            skip_invalid=args.skip_invalid,
            parquet=args.parquet,
        )
    except BuildError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1
    if result.report and result.report.issues:
        print(f"{result.report.summary()} (details: edictai validate)", file=sys.stderr)
    rel = os.path.relpath(result.out_dir)
    print(f"Wrote {rel}/edictai.db, csv/, datapackage.json")
    for table in (
        "jurisdictions",
        "organizations",
        "legislation",
        "documents",
        "executive_actions",
        "research",
    ):
        print(f"  {table}: {result.counts.get(table, 0)}")
    return 0


def cmd_docs(args: argparse.Namespace) -> int:
    paths = _paths(args)
    if args.check:
        if docs.write(paths, check=True):
            return 0
        print(
            f"{os.path.relpath(paths.dictionary)} is out of date; run `edictai docs`",
            file=sys.stderr,
        )
        return 1
    docs.write(paths)
    print(f"Wrote {os.path.relpath(paths.dictionary)}")
    return 0


def _since(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM-DD date") from None


def _env(paths: Paths, name: str) -> str:
    from dotenv import load_dotenv

    load_dotenv(paths.root / ".env")
    return os.environ.get(name, "")


def cmd_ingest_legiscan(args: argparse.Namespace) -> int:
    from .ingest.legiscan import LegiScanClient, LegiScanError, bulk_download

    paths = _paths(args)
    cache = paths.cache / "legiscan"
    try:
        client = LegiScanClient(_env(paths, "LEGISCAN_API_KEY"), cache)
        result = bulk_download(
            client,
            args.since,
            states=args.state,
            progress=lambda m: print(m, file=sys.stderr),
        )
    except LegiScanError as exc:
        print(f"legiscan: {exc}", file=sys.stderr)
        return 1
    sessions = sorted(
        {f"{d['state']} {d['identifier']}" for d in result.downloaded + result.unchanged}
    )
    print(
        f"Datasets: {len(result.downloaded)} downloaded, {len(result.unchanged)} unchanged "
        f"({len(sessions)} sessions since {args.since.isoformat()})"
    )
    print(f"Queries: {result.queries} this run, {result.month_total} this month")
    print(f"Cache: {os.path.relpath(cache)}")
    return 0


def cmd_ingest_search(args: argparse.Namespace) -> int:
    from .ingest.fulltext import run_searches
    from .ingest.legiscan import LegiScanClient, LegiScanError

    paths = _paths(args)
    years = range(args.since.year, date.today().year + 1)
    try:
        client = LegiScanClient(_env(paths, "LEGISCAN_API_KEY"), paths.cache / "legiscan")
        result = run_searches(client, years, progress=lambda m: print(m, file=sys.stderr))
    except LegiScanError as exc:
        print(f"legiscan: {exc}", file=sys.stderr)
        return 1
    print(
        f"{len(result.hits)} bills from {result.searches} search pages; "
        f"{len(result.fetched_bills)} fetched with getBill"
    )
    print(f"Queries: {result.queries} this run, {result.month_total} this month")
    return 0


def cmd_ingest_candidates(args: argparse.Namespace) -> int:
    from .ingest.candidates import existing_records, find_candidates, listed_sessions

    paths = _paths(args)
    result = find_candidates(
        paths.cache / "legiscan",
        paths.cache / "candidates",
        args.since,
        existing=existing_records(paths.data),
        listed=listed_sessions(paths.data),
    )
    print(
        f"{result.total} candidates from {result.bills_scanned} bills "
        f"in {result.datasets} datasets -> {os.path.relpath(result.path)}"
    )
    print(
        "Matched by: "
        + ", ".join(f"{k} {v}" for k, v in result.by_matched.most_common())
        + "; trigger strength: "
        + ", ".join(f"{k} {v}" for k, v in result.by_strength.most_common())
    )
    print(f"Already in data/legislation: {len(result.existing)}")
    if result.fulltext_unresolved:
        print(f"  {len(result.fulltext_unresolved)} full-text hits could not be resolved")
    if result.missing_datasets:
        print(f"  {len(result.missing_datasets)} listed sessions have no cached dataset")
    print("By year: " + ", ".join(f"{y}: {n}" for y, n in sorted(result.by_year.items())))
    print("By state:")
    for state, n in result.by_state.most_common():
        years = ", ".join(
            f"{y}: {c}" for (s, y), c in sorted(result.by_state_year.items()) if s == state
        )
        print(f"  {state} {n} ({years})")
    return 0


def cmd_ingest_sessions(args: argparse.Namespace) -> int:
    from .ingest.sessions import OpenStatesClient, OpenStatesError, update_sessions

    paths = _paths(args)
    try:
        client = OpenStatesClient(_env(paths, "OPENSTATES_API_KEY"), paths.cache / "openstates")
        result = update_sessions(paths, client, dry_run=args.dry_run)
    except OpenStatesError as exc:
        print(f"openstates: {exc}", file=sys.stderr)
        return 1
    verb = "Would update" if args.dry_run else "Updated"
    for c in result.changes:
        fields = ", ".join(f"{k} {v}" for k, v in c.fields.items())
        print(
            f"{verb} {c.jurisdiction} {c.identifier}: {fields} (Open States {c.openstates_identifier!r})"
        )
    for jur, ident, why in result.skipped:
        print(f"Skipped {jur} {ident}: {why}")
    print(
        f"{len(result.changes)} sessions filled, {len(result.skipped)} skipped, "
        f"{len(result.complete)} already complete; {result.requests} Open States requests"
    )
    return 0


def cmd_ingest_federal(args: argparse.Namespace) -> int:
    from .federal.congress import CongressClient, CongressError
    from .federal.federal_register import FederalRegisterClient, FederalRegisterError
    from .federal.run import find_federal_candidates

    paths = _paths(args)
    try:
        congress = (
            None
            if args.skip_congress
            else CongressClient(_env(paths, "CONGRESS_API_KEY"), paths.cache / "congress")
        )
        fr = (
            None
            if args.skip_federal_register
            else FederalRegisterClient(paths.cache / "federal_register")
        )
        result = find_federal_candidates(
            paths.data,
            paths.cache / "candidates",
            congress=congress,
            federal_register=fr,
            congresses=args.congress or (118, 119),
            since=args.since,
            progress=lambda m: print(m, file=sys.stderr, flush=True),
            titles=args.titles,
            title_requests=args.title_requests,
        )
    except (CongressError, FederalRegisterError) as exc:
        print(f"federal: {exc}", file=sys.stderr)
        return 1
    s = result.summary()
    print(
        f"{result.bills} bills, {result.documents} Federal Register documents "
        f"-> {os.path.relpath(result.path)}"
    )
    print("Bills by Congress: " + ", ".join(f"{k}: {v}" for k, v in s["bills_by_congress"].items()))
    print("Documents: " + ", ".join(f"{k}: {v}" for k, v in s["documents_by_type"].items()))
    print(f"Already in data/: {len(result.existing)}; not found: {len(result.missing_existing)}")
    print(
        f"Queries: Congress.gov {result.congress_queries} ({result.congress_cache_hits} cached), "
        f"Federal Register {result.fr_queries} ({result.fr_cache_hits} cached)"
    )
    if deferred := sum(result.titles_deferred.values()):
        print(f"Titles left for a later run (request limit reached): {deferred}")
    for err in result.errors:
        print(f"  error: {err}", file=sys.stderr)
    return 0


RESEARCH_SOURCES = ("openalex", "eric")


def cmd_ingest_research(args: argparse.Namespace) -> int:
    """Research discovery is not enabled: its sources have not been approved for use."""
    names = ", ".join(args.source or RESEARCH_SOURCES)
    print(
        f"Research discovery ({names}) is not enabled: these sources have not been approved "
        "for use. Nothing was fetched."
    )
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    import json

    from .watch import run as watch_run

    report, md = watch_run(
        _paths(args),
        since=args.since,
        org_ids=args.org or None,
        urls=args.url or None,
        limit=args.limit,
        save=not args.no_save,
    )
    if args.json:
        Path(args.json).write_text(json.dumps(report.to_dict(), indent=2) + "\n")
    if args.markdown:
        Path(args.markdown).write_text(md)
    else:
        print(md)
    return 1 if report.has_changes else 0


def cmd_watch_apply(args: argparse.Namespace) -> int:
    """Exits 0 when every page was reviewed or left for the next run; 2 when the
    settings cannot be read or Claude Code cannot run."""
    import json

    from dotenv import load_dotenv

    from .classify.llm import ClaudeCodeError, spend_line
    from .classify.settings import SettingsError, load_settings
    from .watch.apply import apply_changes, load_apply_settings, render_markdown, render_review

    paths = _paths(args)
    load_dotenv(paths.root / ".env")
    try:
        settings = load_settings(paths)
        asettings = load_apply_settings(paths)
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.model:
        asettings = type(asettings)(**{**asettings.__dict__, "model": args.model})
    try:
        result = apply_changes(
            paths,
            settings,
            asettings,
            urls=args.url or None,
            limit=args.limit,
            save=not args.dry_run,
            log=lambda m: print(m, file=sys.stderr, flush=True),
        )
    except (SettingsError, ClaudeCodeError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.json:
        Path(args.json).write_text(json.dumps(result.to_dict(), indent=1) + "\n")
    md = render_markdown(result)
    if args.markdown:
        Path(args.markdown).write_text(md)
    else:
        print(md)
    if args.review:
        Path(args.review).write_text(render_review(result))
    if result.spend:
        print(spend_line(result.spend), file=sys.stderr)
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="edictai", description=__doc__)
    p.add_argument("--root", help="repository root (default: nearest directory with data/schema)")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser(
        "validate", help="check records against schemas, vocabularies, and references"
    )
    v.add_argument(
        "--strict",
        action="store_true",
        help="treat references to missing organization files as errors",
    )
    v.add_argument("--format", choices=("text", "github", "json"), default="text")
    v.set_defaults(func=cmd_validate)

    b = sub.add_parser("build", help="build the database and exports")
    b.add_argument("--out", help="output directory (default: build/)")
    b.add_argument(
        "--skip-invalid",
        action="store_true",
        help="build from the valid records instead of stopping on validation errors",
    )
    # Accepted and ignored, for build commands written before the snapshot was removed.
    b.add_argument("--no-web", action="store_true", help=argparse.SUPPRESS)
    pq = b.add_mutually_exclusive_group()
    pq.add_argument(
        "--parquet",
        dest="parquet",
        action="store_true",
        default=None,
        help="require Parquet output",
    )
    pq.add_argument(
        "--no-parquet", dest="parquet", action="store_false", help="skip Parquet output"
    )
    b.set_defaults(func=cmd_build)

    d = sub.add_parser("docs", help="regenerate docs/data/dictionary.md")
    d.add_argument("--check", action="store_true", help="fail if the dictionary is out of date")
    d.set_defaults(func=cmd_docs)

    ing = sub.add_parser("ingest", help="fetch source data into .cache/")
    isub = ing.add_subparsers(dest="source", required=True)
    lg = isub.add_parser(
        "legiscan", help="download LegiScan session datasets that changed since the last run"
    )
    lg.add_argument(
        "--since",
        type=_since,
        default=date(2023, 1, 1),
        help="first date of the window; sessions overlapping it are fetched (default 2023-01-01)",
    )
    lg.add_argument("--state", action="append", help="limit to a state (repeatable), e.g. TX, US")
    lg.set_defaults(func=cmd_ingest_legiscan)
    sr = isub.add_parser(
        "search", help="run the LegiScan full-text searches and fetch bills missing from datasets"
    )
    sr.add_argument("--since", type=_since, default=date(2023, 1, 1))
    sr.set_defaults(func=cmd_ingest_search)
    ca = isub.add_parser("candidates", help="list AI-related bills from the cached datasets")
    ca.add_argument("--since", type=_since, default=date(2023, 1, 1))
    ca.set_defaults(func=cmd_ingest_candidates)
    se = isub.add_parser(
        "sessions", help="fill missing session dates in data/jurisdictions from Open States"
    )
    se.add_argument("--dry-run", action="store_true", help="report without writing files")
    se.set_defaults(func=cmd_ingest_sessions)
    fe = isub.add_parser(
        "federal", help="list AI-related bills (Congress.gov) and Federal Register documents"
    )
    fe.add_argument("--since", type=_since, default=date(2023, 1, 1))
    fe.add_argument(
        "--congress",
        type=int,
        action="append",
        help="limit to a Congress (repeatable; default 118 and 119)",
    )
    fe.add_argument("--skip-congress", action="store_true")
    fe.add_argument("--skip-federal-register", action="store_true")
    fe.add_argument(
        "--titles",
        action="store_true",
        help="also match every bill's official and short titles (one request per bill; cached)",
    )
    fe.add_argument(
        "--title-requests",
        type=int,
        metavar="N",
        help="with --titles, send at most N uncached title requests; the rest wait for a later run",
    )
    fe.set_defaults(func=cmd_ingest_federal)
    rs = isub.add_parser(
        "research", help="find new research (OpenAlex, ERIC); not enabled, fetches nothing"
    )
    rs.add_argument("--source", action="append", choices=RESEARCH_SOURCES)
    rs.set_defaults(func=cmd_ingest_research)

    w = sub.add_parser(
        "watch", help="check watched guidance pages for changes; exits 1 when something changed"
    )
    w.add_argument("--since", type=_since, help="report every change logged on or after this date")
    w.add_argument("--org", action="append", help="limit to an organization id (repeatable)")
    w.add_argument("--url", action="append", help="limit to a URL (repeatable)")
    w.add_argument("--limit", type=int, help="fetch at most this many pages")
    w.add_argument("--json", help="write the report as JSON to this path")
    w.add_argument("--markdown", help="write the PR body to this path (default: stdout)")
    w.add_argument("--no-save", action="store_true", help="do not update .cache/watch state")
    w.set_defaults(func=cmd_watch)

    wa = sub.add_parser(
        "watch-apply",
        help="review the changes the watcher logged with Claude and update the records",
    )
    wa.add_argument("--url", action="append", help="limit to a URL (repeatable)")
    wa.add_argument("--limit", type=int, help="review at most this many pages")
    wa.add_argument("--model", help="model id (default: watch.model in pipeline/automation.yaml)")
    wa.add_argument("--json", help="write the result as JSON to this path")
    wa.add_argument("--markdown", help="write the PR body to this path (default: stdout)")
    wa.add_argument("--review", help="write what needs a person to this path (empty if nothing)")
    wa.add_argument(
        "--dry-run",
        action="store_true",
        help="ask Claude but write neither records nor .cache/watch state",
    )
    wa.set_defaults(func=cmd_watch_apply)

    from .classify.commands import register as register_classify

    register_classify(sub)

    from .articles.commands import register as register_articles

    register_articles(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
