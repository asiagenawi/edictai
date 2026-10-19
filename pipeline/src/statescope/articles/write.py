"""`articles write`: one article from the week's data, or a recorded reason why not.

1. If the ledger already has an article written for this run date, it is restored
   from the cache when its file is missing and nothing is sent.
2. The subject is chosen (`subjects.choose`) and its data prepared (`context`).
3. A drafting call writes the article; a second, separate call edits and
   fact-checks it against the records and studies its notes cite. Both go through
   the classifier's `Judge`, under the run's spending cap and the article's own cap.
4. The edited article is checked (`check`). When it fails, the editor is asked once
   more with the failures listed (`repair_attempts`).
5. The file is written to site/src/content/articles/<year>/<slug>.yaml and the site
   is built. When the build fails the file is removed.

Any failure skips the article: nothing is written, and the reason is in the result
and the ledger. The data changes of the run are not affected.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..classify.llm import Budget, CappedBudget, Judge, Request
from ..classify.settings import Settings, SettingsError, settings_path
from ..paths import Paths
from ..yamlio import load_yaml
from .check import check_article, from_reply, word_count
from .context import build_context
from .data import Dataset, changed_ids, load_articles, load_dataset
from .prompts import (
    DRAFT_SYSTEM,
    DRAFT_USER,
    EDIT_SYSTEM,
    EDIT_USER,
    REPAIR,
    reply_schema,
)
from .subjects import (
    Limits,
    candidates,
    choose,
    choose_drawing,
    coverage,
    ledger_entry,
    read_ledger,
    write_ledger_entry,
)
from .yamlout import dump_article

EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ArticleSettings:
    model: str = "claude-opus-5-5"
    draft_effort: str = "high"
    edit_effort: str = "high"
    max_tokens: Mapping[str, int] = field(default_factory=lambda: {"draft": 32000, "edit": 32000})
    spend_cap_usd: float = 3.0
    min_words: int = 600
    max_words: int = 1200
    target_words: int = 850
    window_days: int = 7
    max_records: int = 60
    repair_attempts: int = 1
    build_timeout_minutes: float = 15
    limits: Limits = field(default_factory=Limits)

    def with_overrides(self, **values: Any) -> ArticleSettings:
        clean = {k: v for k, v in values.items() if v is not None}
        return ArticleSettings(**{**self.__dict__, **clean})


def load_article_settings(paths: Paths) -> ArticleSettings:
    """The `articles` section of pipeline/automation.yaml (defaults when absent)."""
    path = settings_path(paths)
    data = (load_yaml(path) or {}) if path.exists() else {}
    a = data.get("articles") or {}
    base = ArticleSettings()
    lim = {**base.limits.__dict__, **(a.get("subjects") or {})}
    out = ArticleSettings(
        model=a.get("model", base.model),
        draft_effort=a.get("draft_effort", base.draft_effort),
        edit_effort=a.get("edit_effort", base.edit_effort),
        max_tokens={**base.max_tokens, **(a.get("max_tokens") or {})},
        spend_cap_usd=float(a.get("spend_cap_usd", base.spend_cap_usd)),
        min_words=int(a.get("min_words", base.min_words)),
        max_words=int(a.get("max_words", base.max_words)),
        target_words=int(a.get("target_words", base.target_words)),
        window_days=int(a.get("window_days", base.window_days)),
        max_records=int(a.get("max_records", base.max_records)),
        repair_attempts=int(a.get("repair_attempts", base.repair_attempts)),
        build_timeout_minutes=float(a.get("build_timeout_minutes", base.build_timeout_minutes)),
        limits=Limits(**{k: int(v) for k, v in lim.items()}),
    )
    for k in ("draft_effort", "edit_effort"):
        if getattr(out, k) not in EFFORTS:
            raise SettingsError(f"articles.{k} must be one of {', '.join(EFFORTS)}")
    return out


@dataclass
class ArticleResult:
    status: str  # written, exists, skipped
    run_date: str
    reason: str | None = None
    subject: dict[str, Any] | None = None
    type: str | None = None
    slug: str | None = None
    path: str | None = None
    title: str | None = None
    dek: str | None = None
    words: int | None = None
    notes: list[dict[str, Any]] = field(default_factory=list)
    edits: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    spend: dict[str, Any] = field(default_factory=dict)
    model: str | None = None

    @property
    def citations(self) -> dict[str, Any]:
        recs = sorted({n["record"] for n in self.notes if "record" in n})
        studies = sorted({n["study"] for n in self.notes if "study" in n})
        return {"notes": len(self.notes), "records": recs, "studies": studies}

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "run_date": self.run_date,
            "reason": self.reason,
            "subject": self.subject,
            "type": self.type,
            "slug": self.slug,
            "path": self.path,
            "title": self.title,
            "dek": self.dek,
            "words": self.words,
            "citations": self.citations,
            "edits": self.edits,
            "errors": self.errors,
            "spend": self.spend,
            "model": self.model,
        }

    def ledger(self) -> dict[str, Any]:
        return {
            "run_date": self.run_date,
            "status": "written" if self.status == "exists" else self.status,
            "type": self.type,
            "subject": (self.subject or {}).get("key"),
            "window": (self.subject or {}).get("window"),
            "slug": self.slug,
            "path": self.path,
            "title": self.title,
            "reason": self.reason,
            "spent_usd": self.spend.get("spent_usd"),
        }


Builder = Callable[[Path], tuple[bool, str]]


def npm_build(paths: Paths, timeout_minutes: float) -> Builder:
    """`npm run build` in the site directory, against this repository's build/."""

    def run(site: Path) -> tuple[bool, str]:
        npm = shutil.which("npm")
        if npm is None:
            return False, "npm is not installed"
        env = {**os.environ, "EDICTAI_BUILD_DIR": str(paths.root / "build")}
        try:
            proc = subprocess.run(
                [npm, "run", "build"],
                cwd=site,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_minutes * 60,
            )
        except subprocess.TimeoutExpired:
            return False, f"the site build took longer than {timeout_minutes} minutes"
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:])
        return proc.returncode == 0, tail

    return run


def fill(template: str, **values: Any) -> str:
    """Replace {name} for the names given only (the templates hold literal braces)."""
    for k, v in values.items():
        template = template.replace("{" + k + "}", str(v))
    return template


def _cells_of_reply(reply: Mapping[str, Any]) -> list[str]:
    out = []
    for b in reply.get("body") or []:
        if isinstance(b, Mapping) and isinstance(b.get("table"), Mapping):
            for row in b["table"].get("rows") or []:
                out += [c.get("record") for c in row if isinstance(c, Mapping) and c.get("record")]
    return out


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


def write_article(
    paths: Paths,
    settings: Settings,
    asettings: ArticleSettings,
    budget: Budget,
    *,
    run_date: date,
    site: Path,
    ledger: Path,
    changed_paths: Iterable[str] = (),
    client: Any = None,
    build: Builder | None = None,
    kind: str | None = None,
    subject_key: str | None = None,
    store: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> ArticleResult:
    log = log or (lambda _m: None)
    day = run_date.isoformat()
    content = site / "src" / "content" / "articles"
    store = store or paths.cache / "articles" / "written"
    build = build or npm_build(paths, asettings.build_timeout_minutes)
    result = ArticleResult("skipped", day, model=asettings.model)

    def skip(reason: str) -> ArticleResult:
        result.status, result.reason = "skipped", reason
        write_ledger_entry(ledger, result.ledger())
        log(f"article skipped: {reason}")
        return result

    # 1. Already written for this run date.
    done = ledger_entry(ledger, day)
    if done and done.get("status") == "written" and done.get("path"):
        target = site / done["path"]
        saved = store / f"{done['slug']}.yaml"
        if not target.exists() and saved.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(saved, target)
            ok, out = build(site)
            if not ok:
                target.unlink()
                return skip(f"the article written earlier today no longer builds: {out}")
        if target.exists():
            data = load_yaml(target) or {}
            result.status = "exists"
            result.subject = {"key": done.get("subject"), "window": done.get("window")}
            result.type, result.slug, result.path = done.get("type"), done["slug"], done["path"]
            result.title, result.dek = data.get("title"), data.get("dek")
            result.words, result.notes = word_count(data), list(data.get("notes") or [])
            return result

    # 2. Subject and data.
    try:
        ds: Dataset = load_dataset(paths.root / "build" / "statescope.db")
    except FileNotFoundError as exc:
        return skip(str(exc))
    articles = load_articles(content)
    history = read_ledger(ledger)
    changed = changed_ids(
        ds, changed_paths, since=run_date - timedelta(days=asettings.window_days), until=run_date
    )
    cover = coverage(ds, articles, history)
    subject = choose(
        candidates(ds, cover, changed, run_date=run_date, limits=asettings.limits),
        cover,
        kind=kind,
        key=subject_key,
    )
    if subject is None:
        return skip("no subject left that the existing articles do not cover")
    result.subject, result.type = subject.as_dict(), subject.type
    log(f"subject: {subject.type}, {subject.key} ({subject.changed} changed this week)")
    ctx = build_context(
        ds,
        subject,
        changed,
        articles,
        run_date=run_date,
        max_records=asettings.max_records,
        cited_studies=cover.studies,
    )

    drawing = choose_drawing(ds, subject, articles)

    # 3. Draft and edit.
    try:
        base = settings.with_overrides(model=asettings.model, use_batches=False)
        base.price()
    except SettingsError as exc:
        return skip(str(exc))
    sub = CappedBudget(budget, asettings.spend_cap_usd)
    drafter = Judge(base.with_overrides(effort=asettings.draft_effort), sub, client, log=log)
    editor = Judge(base.with_overrides(effort=asettings.edit_effort), sub, drafter._client, log=log)
    words = {
        "min_words": asettings.min_words,
        "max_words": asettings.max_words,
        "target_words": asettings.target_words,
    }
    labels = {"type": subject.type, "label": subject.label, "as_of": ctx.as_of}

    def finish_spend() -> None:
        result.spend = {**sub.as_dict(), "model": asettings.model}

    draft = drafter.call(
        Request(
            "article-draft",
            fill(DRAFT_SYSTEM, **words),
            fill(
                DRAFT_USER,
                **labels,
                run_date=day,
                data_release=ctx.release,
                data=_dumps(ctx.payload),
            ),
            reply_schema(),
            int(asettings.max_tokens.get("draft", 32000)),
        ),
        stream=True,
    )
    finish_spend()
    if draft.error or not isinstance(draft.data, dict):
        return skip(f"draft: {draft.error or 'no article returned'}")

    edit_system = fill(EDIT_SYSTEM, **words)
    current = draft.data
    edits: list[str] = []
    errors: list[str] = []
    slug, article = "", {}
    existing = [a.slug for a in articles]
    for attempt in range(1 + max(0, asettings.repair_attempts)):
        cited = ctx.cited(current.get("notes") or [], _cells_of_reply(current))
        user = fill(
            EDIT_USER,
            **labels,
            draft=_dumps({k: v for k, v in current.items() if k != "edits"}),
            data=_dumps(cited),
        )
        if attempt:
            user += fill(REPAIR, errors="\n".join(f"- {e}" for e in errors))
        reply = editor.call(
            Request(
                f"article-edit-{attempt + 1}",
                edit_system,
                user,
                reply_schema(edits=True),
                int(asettings.max_tokens.get("edit", 32000)),
            ),
            stream=True,
        )
        finish_spend()
        if reply.error or not isinstance(reply.data, dict):
            return skip(f"edit: {reply.error or 'no article returned'}")
        current = reply.data
        edits += [str(e) for e in current.get("edits") or []]
        slug, article = from_reply(current, type=subject.type, published=day, release=ctx.release)
        article["drawing"] = drawing
        errors = check_article(
            slug,
            article,
            ctx,
            type=subject.type,
            published=day,
            existing_slugs=existing,
            min_words=asettings.min_words,
            max_words=asettings.max_words,
        )
        if not errors:
            break
        log(f"edit {attempt + 1}: {len(errors)} checks failed")
    result.slug, result.title, result.dek = slug or None, article.get("title"), article.get("dek")
    result.words, result.notes, result.edits = (
        word_count(article),
        article.get("notes") or [],
        edits,
    )
    if errors:
        result.errors = errors
        return skip(f"the article failed {len(errors)} checks: " + "; ".join(errors[:5]))

    # 4. Write and build.
    rel = Path("src") / "content" / "articles" / str(run_date.year) / f"{slug}.yaml"
    target = site / rel
    made_dir = not target.parent.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = dump_article(article)
    target.write_text(text, encoding="utf-8")
    ok, out = build(site)
    if not ok:
        target.unlink()
        if made_dir and not any(target.parent.iterdir()):
            target.parent.rmdir()
        return skip(f"the site build failed with the article: {out}")
    store.mkdir(parents=True, exist_ok=True)
    (store / f"{slug}.yaml").write_text(text, encoding="utf-8")
    result.status, result.path = "written", str(rel)
    write_ledger_entry(ledger, result.ledger())
    log(f"article written: {rel}")
    return result
