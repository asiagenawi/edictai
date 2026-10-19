"""`statescope articles ...` subcommands."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from ..paths import Paths


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def cmd_write(args: argparse.Namespace) -> int:
    """Exits 0 whether or not an article was written; the result says which and why.
    Exits 2 only when the settings cannot be read."""
    from dotenv import load_dotenv

    from ..classify.llm import make_budget, spend_line
    from ..classify.settings import SettingsError, load_settings
    from ..classify.summary import git_changes
    from .write import ArticleResult, load_article_settings, write_article

    paths = Paths.discover(args.root)
    load_dotenv(paths.root / ".env")
    try:
        settings = load_settings(paths).with_overrides(spend_cap_usd=args.spend_cap)
        asettings = load_article_settings(paths).with_overrides(
            model=args.model, spend_cap_usd=args.article_cap
        )
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return 2
    run_date = date.fromisoformat(args.date) if args.date else date.today()
    site = Path(args.site).resolve() if args.site else paths.root / "site"
    ledger = (
        Path(args.ledger).resolve() if args.ledger else paths.cache / "articles" / "ledger.json"
    )
    spend = Path(args.spend_file).resolve() if args.spend_file else None
    try:
        changed = [p for pair in git_changes(paths.root) for p in pair]
    except (OSError, subprocess.CalledProcessError) as exc:
        _log(f"no changed files from git ({type(exc).__name__}); using dated activity only")
        changed = []
    build = None
    if args.no_build:
        build = lambda _site: (True, "build skipped (--no-build)")  # noqa: E731
    try:
        result = write_article(
            paths,
            settings,
            asettings,
            make_budget(settings, spend),
            run_date=run_date,
            site=site,
            ledger=ledger,
            changed_paths=changed,
            build=build,
            kind=args.type,
            subject_key=args.subject,
            log=_log,
        )
    except Exception as exc:  # the data updates of the run must not depend on the article
        result = ArticleResult(
            "skipped", run_date.isoformat(), reason=f"{type(exc).__name__}: {exc}"
        )
        _log(f"article skipped: {result.reason}")
    out = result.as_dict()
    if result.path:
        full = site / result.path
        rel = os.path.relpath(full, paths.root)
        out["path"] = str(full) if rel.startswith("..") else rel
    if result.status == "skipped":
        print(f"No article: {result.reason}")
    else:
        print(
            f"{'Wrote' if result.status == 'written' else 'Already written'}: {out['path']} "
            f"({result.type}, {result.words} words, {len(result.notes)} notes)"
        )
    if result.spend:
        print(f"Article spending: {spend_line(result.spend)}")
    if args.result:
        Path(args.result).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    a = sub.add_parser("articles", help="draft, check, and write articles from the data")
    asub = a.add_subparsers(dest="action", required=True)
    from .subjects import TYPES

    p = asub.add_parser(
        "write",
        help="write this week's article: draft, edit and fact-check, check, and build the site",
    )
    p.add_argument("--model", help="model id (default: articles.model in pipeline/automation.yaml)")
    p.add_argument("--type", choices=TYPES, help="write this type of article")
    p.add_argument("--subject", help="write on this subject key, e.g. jurisdiction:us-tx")
    p.add_argument("--date", help="run date, YYYY-MM-DD (default: today)")
    p.add_argument(
        "--spend-cap",
        type=float,
        help="dollar cap for the whole run (default: WEEKLY_SPEND_CAP_USD, else the settings file)",
    )
    p.add_argument(
        "--article-cap",
        type=float,
        help="dollar cap for this article (default: articles.spend_cap_usd)",
    )
    p.add_argument(
        "--spend-file", help="JSON ledger of dollars spent, shared by the commands of one run"
    )
    p.add_argument("--site", help="site directory to write into and build (default: site/)")
    p.add_argument("--ledger", help="subject ledger (default: .cache/articles/ledger.json)")
    p.add_argument("--no-build", action="store_true", help="skip the site build (local use only)")
    p.add_argument("--result", help="write the result as JSON to this path")
    p.set_defaults(func=cmd_write)
