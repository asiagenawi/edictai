"""`watch-apply`: bring the records up to date with the changes the watcher logged.

1. A watched page is pending when the watcher logged a change for it and its
   current hash is not the one the records were last reviewed against
   (`WatchState.applied`). Every change since that review is handled at once.
2. For each pending page, Claude (through the classifier's `Judge`) is given the
   page's text before and after, the document records that cite it, the
   issuer's other documents, and the text of documents the page links to, and
   replies with a verdict and the record changes (see prompts.py).
3. Every change must be backed by quotes found in the supplied texts; a change
   with a quote that is not found is dropped and listed for review. Updated and
   new records are written and the data is validated; a record that fails is
   put back as it was.
4. The page is marked reviewed (its hash and text become the new base), so the
   next run sees only later changes. A page whose request went unanswered stays
   pending for the next run.
"""

from __future__ import annotations

import copy
import json
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..classify.instructions import scope_section
from ..classify.llm import Judge, Request, make_budget
from ..classify.settings import Settings, SettingsError, settings_path
from ..classify.textutil import quote_in_text
from ..classify.yamlwrite import dump
from ..paths import Paths
from ..records import Record, load_records
from ..validate import validate
from ..vocab import Vocab
from ..yamlio import load_yaml
from .diff import diff_excerpt
from .extract import extract
from .fetch import Fetcher, Transport, urllib_transport
from .mapping import find_documents_for_url, known_urls, normalize_url
from .prompts import reply_schema, system_prompt
from .run import watch_targets
from .state import WatchState

EFFORTS = ("low", "medium", "high", "xhigh", "max")
METHOD = "change-watcher"
QUOTE_LIMIT = 600  # the schema's citation quote maxLength
# Fields of a document record an update may set.
UPDATE_FIELDS = (
    "title",
    "summary",
    "status",
    "superseded_by",
    "published",
    "updated",
    "version",
    "topics",
)


@dataclass(frozen=True)
class ApplySettings:
    model: str = "claude-opus-5-5"
    effort: str = "high"
    max_tokens: int = 16000
    # Claude Code calls one run may use; a reply asked once more counts twice.
    claude_code_calls: int = 60
    # Dollars one run may spend with the API backend.
    spend_cap_usd: float = 10.0
    # Characters of each text given to the model; quotes are checked against the whole text.
    max_text_chars: int = 60000
    # Linked documents fetched per page: new AI links, and the other sources of the
    # records that cite the page, each up to this many.
    max_linked: int = 4


def load_apply_settings(paths: Paths) -> ApplySettings:
    """The `watch` section of pipeline/automation.yaml (defaults when absent)."""
    path = settings_path(paths)
    data = (load_yaml(path) or {}) if path.exists() else {}
    w = data.get("watch") or {}
    base = ApplySettings()
    try:
        out = ApplySettings(
            **{k: type(getattr(base, k))(w.get(k, getattr(base, k))) for k in asdict(base)}
        )
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"watch: {exc}") from exc
    if out.effort not in EFFORTS:
        raise SettingsError(f"watch.effort must be one of {', '.join(EFFORTS)}")
    return out


# -- pending pages ---------------------------------------------------------------


@dataclass
class Page:
    url: str
    org: Record
    changes: list[dict[str, Any]]  # logged since the last review, oldest first
    entry: dict[str, Any]  # the URL's state entry

    @property
    def observed(self) -> str:
        return self.changes[-1]["observed"]


def pending_pages(state: WatchState, organizations: list[Record]) -> list[Page]:
    """Watched pages with a logged change the records have not been reviewed against."""
    orgs = {r.id: r for r in organizations}
    logged: dict[str, list[dict[str, Any]]] = {}
    for change in state.changes:
        logged.setdefault(change["url"], []).append(change)
    pages = []
    for org_id, url in watch_targets(organizations):
        entry = state.urls.get(url) or {}
        current = entry.get("sha256")
        done = state.applied.get(url) or {}
        if url not in logged or not current or done.get("sha256") == current:
            continue
        since = done.get("applied") or ""
        changes = [c for c in logged[url] if c["observed"] >= since] or logged[url][-1:]
        pages.append(Page(url, orgs[org_id], changes, entry))
    return pages


# -- texts -------------------------------------------------------------------------


def fetch_text(fetcher: Fetcher, url: str) -> tuple[str | None, str | None]:
    """(text, None) or (None, why not)."""
    result = fetcher.get(url)
    if result.outcome != "ok":
        return None, result.error or result.outcome
    extracted = extract(result.body, result.content_type, result.final_url or url)
    if not extracted.text:
        return None, "no text could be extracted"
    return extracted.text, None


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + f"\n… (cut at {n} of {len(text)} characters)"


def _find(texts: Mapping[str, str], url: str) -> tuple[str, str] | None:
    """(the supplied URL, its text) for a URL as the model wrote it."""
    key = normalize_url(url or "")
    for u, text in texts.items():
        if normalize_url(u) == key:
            return u, text
    return None


def _check_quote(texts: Mapping[str, str], source_url: str, quote: str) -> str | None:
    """Why a quote fails, or None when it is found in its source's text."""
    found = _find(texts, source_url)
    if found is None:
        return f"quote cites {source_url}, which is not one of the supplied texts"
    if len(quote) > QUOTE_LIMIT:
        return f"quote longer than {QUOTE_LIMIT} characters"
    if not quote.strip() or not quote_in_text(quote, found[1]):
        return f"quote not found in {found[0]}: {quote[:120]!r}"
    return None


def _check_quotes(
    texts: Mapping[str, str], item: Mapping[str, Any], provisions_key: str
) -> list[str]:
    evidence = item.get("evidence") or []
    errors = [] if evidence else ["no evidence quoted"]
    for e in evidence:
        if why := _check_quote(texts, e.get("source_url", ""), e.get("quote", "")):
            errors.append(why)
    for p in item.get(provisions_key) or []:
        if why := _check_quote(texts, p.get("source_url", ""), p.get("quote", "")):
            errors.append(f"provision {p.get('type')}: {why}")
    return errors


def _provision(p: Mapping[str, Any], texts: Mapping[str, str]) -> dict[str, Any]:
    url = (_find(texts, p["source_url"]) or (p["source_url"], ""))[0]
    out: dict[str, Any] = {"type": p["type"], "description": p["description"]}
    if p.get("education_levels"):
        out["education_levels"] = list(p["education_levels"])
    citation = {"source_url": url}
    if p.get("locator"):
        citation["locator"] = p["locator"]
    citation["quote"] = p["quote"]
    out["citation"] = citation
    return out


# -- the request -------------------------------------------------------------------


def _record_yaml(record: Record) -> str:
    return f"## `{record.id}` (data/{record.rel})\n\n```yaml\n{dump(record.data).rstrip()}\n```"


def user_content(
    page: Page,
    *,
    base: str | None,
    current: str,
    citing: list[Record],
    issuer_docs: list[Record],
    linked: Mapping[str, str],
    unreadable: Mapping[str, str],
    max_chars: int,
) -> str:
    org = page.org.data
    dates = ", ".join(sorted({c["observed"] for c in page.changes}))
    out = [
        "# Watched page",
        "",
        f"Issuer: {org.get('name')} (`{page.org.id}`), jurisdiction `{org.get('jurisdiction')}`",
        f"URL: {page.url}",
        f"Changes observed: {dates}",
        "",
        "## What changed",
        "",
    ]
    if base is not None:
        diff = diff_excerpt(
            base, current, context=2, max_hunks=60, max_lines=600, max_line_chars=1000
        )
        out.append(f"<diff>\n{diff or '(no difference in the text)'}\n</diff>")
    else:
        out.append("The earlier text was not kept; these are the diffs logged for each change.")
        for c in page.changes:
            out.append(
                f'\n<diff observed="{c["observed"]}">\n{c.get("diff_excerpt") or ""}\n</diff>'
            )
    out += [
        "",
        "## Current text of the page",
        "",
        f'<text url="{page.url}">\n{_clip(current, max_chars)}\n</text>',
        "",
        "# Records",
        "",
        "Records that cite this page; only these, and the issuer's documents listed below, may be updated.",
        "",
    ]
    out += [_record_yaml(r) for r in citing] or ["(none)"]
    out += ["", "# Other documents from this issuer", ""]
    for r in issuer_docs:
        sources = ", ".join(s.get("url", "") for s in r.data.get("sources") or [])
        out.append(
            f"- `{r.id}`: {r.data.get('title')} ({r.data.get('status')}); sources: {sources}"
        )
    if not issuer_docs:
        out.append("(none)")
    out += ["", "# Linked documents", ""]
    for url, text in linked.items():
        out.append(f'<text url="{url}">\n{_clip(text, max_chars)}\n</text>\n')
    if not linked:
        out.append("(none fetched)")
    for url, why in unreadable.items():
        out.append(f"- Could not be read: {url} ({why})")
    return "\n".join(out) + "\n"


# -- results -----------------------------------------------------------------------


@dataclass
class PageResult:
    url: str
    org_id: str
    verdict: str | None = None  # noise, update, unchanged, unreadable; None when not answered
    what_changed: str = ""
    updated: list[str] = field(default_factory=list)  # record ids
    added: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # changes thrown out, with why
    review: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class ApplyResult:
    run_date: str
    model: str
    pages: list[PageResult] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)  # repository-relative
    spend: dict[str, Any] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.changed_files)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "changed": self.changed}


# -- applying a reply ----------------------------------------------------------------


def _ordered(data: dict[str, Any], order: list[str]) -> dict[str, Any]:
    head = {k: data[k] for k in order if k in data}
    return {**head, **{k: v for k, v in data.items() if k not in head}}


def _revision(page: Page, note: str) -> dict[str, Any]:
    revision: dict[str, Any] = {"observed": page.observed, "url": page.url}
    if page.entry.get("hash_basis", "text") == "text" and page.entry.get("sha256"):
        revision["sha256"] = page.entry["sha256"]
    revision["note"] = note
    return revision


def updated_record(
    data: Mapping[str, Any],
    update: Mapping[str, Any],
    page: Page,
    texts: Mapping[str, str],
    *,
    today: str,
    new_ids: Mapping[str, str],
) -> dict[str, Any]:
    """The record with the update's fields, a revision entry, and fresh verification."""
    out = copy.deepcopy(dict(data))
    for key in UPDATE_FIELDS:
        value = update.get(key)
        if value in (None, "", []):
            continue
        out[key] = new_ids.get(value, value) if key == "superseded_by" else value
    if (update.get("published") or update.get("updated")) and out.get("undated"):
        del out["undated"]
    provisions = [_provision(p, texts) for p in update.get("add_provisions") or []]
    if provisions:
        out["provisions"] = [*(out.get("provisions") or []), *provisions]
    revisions = list(out.get("revisions") or [])
    revision = _revision(page, update["note"])
    if not revisions or revisions[-1].get("sha256") != revision.get("sha256"):
        revisions.append(revision)
    out["revisions"] = revisions
    fetched = {normalize_url(u) for u in texts}
    for source in out.get("sources") or []:
        if isinstance(source, dict) and normalize_url(source.get("url", "")) in fetched:
            source["accessed"] = today
    verification = dict(out.get("verification") or {})
    verification.update(status="machine-verified", last_verified=today, method=METHOD)
    out["verification"] = verification
    return out


def _slug(value: str, jurisdiction: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug.removeprefix(f"{jurisdiction}-")


def new_record(
    doc: Mapping[str, Any],
    page: Page,
    texts: Mapping[str, str],
    *,
    today: str,
    model: str,
) -> dict[str, Any]:
    org = page.org.data
    jurisdiction = org.get("jurisdiction", "")
    source_url = (_find(texts, doc["source_url"]) or (doc["source_url"], ""))[0]
    record: dict[str, Any] = {
        "id": f"{jurisdiction}-{_slug(doc['slug'], jurisdiction)}",
        "jurisdiction": jurisdiction,
        "issuer": page.org.id,
    }
    for key in ("title", "classification", "status", "education_levels", "topics", "summary"):
        record[key] = doc[key]
    for key in ("published", "updated", "version"):
        if doc.get(key):
            record[key] = doc[key]
    provisions = [_provision(p, texts) for p in doc.get("provisions") or []]
    if provisions:
        record["provisions"] = provisions
    if doc.get("related"):
        record["related"] = list(dict.fromkeys(doc["related"]))
    publisher = org.get("name")
    sources = [
        {"url": source_url, "title": doc["source_title"], "publisher": publisher,
         "kind": "official", "accessed": today}
    ]  # fmt: skip
    if normalize_url(source_url) != normalize_url(page.url):
        sources.append(
            {"url": page.url, "publisher": publisher, "kind": "official", "accessed": today}
        )
    record["sources"] = [{k: v for k, v in s.items() if v} for s in sources]
    record["verification"] = {
        "status": "machine-verified",
        "last_verified": today,
        "method": METHOD,
        "notes": (
            f"Found by the change watcher on {page.url} (observed {page.observed}); "
            f"fields checked against the source text by {model}."
        ),
    }
    return record


# -- the run -----------------------------------------------------------------------


@dataclass
class _Write:
    path: Path
    original: str | None  # None for a new file
    page: PageResult
    record_id: str
    added: bool


def _document_order(paths: Paths) -> list[str]:
    schema = json.loads((paths.schema / "document.schema.json").read_text(encoding="utf-8"))
    return list(schema["properties"])


def _write_all(writes: list[_Write], contents: Mapping[Path, dict[str, Any]]) -> None:
    for w in writes:
        w.path.parent.mkdir(parents=True, exist_ok=True)
        w.path.write_text(dump(contents[w.path]), encoding="utf-8")


def _revert(w: _Write) -> None:
    if w.original is None:
        w.path.unlink(missing_ok=True)
    else:
        w.path.write_text(w.original, encoding="utf-8")


def apply_changes(
    paths: Paths,
    settings: Settings,
    asettings: ApplySettings,
    *,
    today: date | None = None,
    cache_dir: Path | None = None,
    urls: Iterable[str] | None = None,
    limit: int | None = None,
    transport: Transport | None = None,
    client: Any = None,
    runner: Callable[..., Any] | None = None,
    save: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] | None = None,
) -> ApplyResult:
    log = log or (lambda _m: None)
    day = (today or date.today()).isoformat()
    result = ApplyResult(day, asettings.model)
    records = load_records(paths.data, ["organization", "document"])
    organizations, documents = records.of("organization"), records.of("document")
    state = WatchState.load(cache_dir or paths.cache / "watch")
    pages = pending_pages(state, organizations)
    if urls is not None:
        wanted = {normalize_url(u) for u in urls}
        pages = [p for p in pages if normalize_url(p.url) in wanted]
    if limit is not None:
        pages = pages[:limit]
    log(f"{len(pages)} page(s) with changes to review")

    vocab = Vocab.load(paths.vocab)
    system = system_prompt(vocab, scope_section(paths.root / "docs" / "methodology.md"))
    schema = reply_schema(vocab)
    fetcher = Fetcher(transport or urllib_transport(), sleep=sleep)
    known = known_urls(documents)

    bases: dict[str, str | None] = {}

    def reviewed(page: Page, verdict: str, current: str | None) -> None:
        state.applied[page.url] = {
            "sha256": page.entry.get("sha256"),
            "applied": day,
            "verdict": verdict,
        }
        bases[page.url] = current

    requests: list[Request] = []
    context: dict[str, tuple[Page, PageResult, dict[str, str], str | None, list[Record]]] = {}
    for i, page in enumerate(pages):
        pr = PageResult(page.url, page.org.id or "")
        result.pages.append(pr)
        current = state.read_text(page.url)
        base = state.read_base(page.url)
        if current is None:
            pr.verdict = "unreadable"
            pr.review.append("The page's text could not be extracted; check it by hand.")
            reviewed(page, pr.verdict, None)
            continue
        if base is not None and base == current:
            pr.verdict = "unchanged"
            pr.what_changed = "The page changed back to the text last reviewed."
            reviewed(page, pr.verdict, current)
            continue
        citing = [m.record for m in find_documents_for_url(page.url, documents)]
        citing_ids = {r.id for r in citing}
        issuer_docs = [
            r for r in documents if r.data.get("issuer") == page.org.id and r.id not in citing_ids
        ]
        # New AI links first, then the other sources of the records that cite the page.
        new_links: list[str] = []
        for change in page.changes:
            for link in change.get("ai_links") or []:
                if normalize_url(link["url"]) not in known and link["url"] not in new_links:
                    new_links.append(link["url"])
        cited: list[str] = []
        for r in citing:
            for s in r.data.get("sources") or []:
                u = s.get("url") if isinstance(s, dict) else None
                if u and normalize_url(u) != normalize_url(page.url) and u not in cited:
                    cited.append(u)
        texts = {page.url: current}
        linked: dict[str, str] = {}
        unreadable: dict[str, str] = {}
        for url in new_links[: asettings.max_linked] + cited[: asettings.max_linked]:
            text, why = fetch_text(fetcher, url)
            if text is None:
                unreadable[url] = why or "unreadable"
            else:
                linked[url] = texts[url] = text
        rid = f"page-{i}"
        requests.append(
            Request(
                rid,
                system,
                user_content(
                    page,
                    base=base,
                    current=current,
                    citing=citing,
                    issuer_docs=issuer_docs,
                    linked=linked,
                    unreadable=unreadable,
                    max_chars=asettings.max_text_chars,
                ),
                schema,
                asettings.max_tokens,
            )
        )
        context[rid] = (page, pr, texts, current, citing + issuer_docs)

    if requests:
        s = settings.with_overrides(
            model=asettings.model,
            effort=asettings.effort,
            use_batches=False,
            call_cap=asettings.claude_code_calls,
            spend_cap_usd=asettings.spend_cap_usd,
        )
        budget = make_budget(s)
        replies = Judge(s, budget, client, runner=runner, log=log).run(requests)
        result.spend = budget.as_dict()
    else:
        replies = {}

    order = _document_order(paths)
    by_id = {r.id: r for r in documents}
    existing_ids = set(by_id)
    contents: dict[Path, dict[str, Any]] = {}
    writes: list[_Write] = []
    for rid, (page, pr, texts, current, allowed) in context.items():
        reply = replies.get(rid)
        if reply is None or reply.error or not isinstance(reply.data, dict):
            pr.error = (reply.error if reply else None) or "no reply"
            log(f"{page.url}: {pr.error}; left for the next run")
            continue
        data = reply.data
        pr.verdict = data.get("verdict")
        pr.what_changed = data.get("what_changed", "")
        pr.review = list(data.get("review") or [])
        reviewed(page, pr.verdict or "noise", current)
        if pr.verdict != "update":
            continue
        allowed_ids = {r.id for r in allowed}
        jurisdiction = page.org.data.get("jurisdiction", "")
        new_ids = {}
        for doc in data.get("new_documents") or []:
            new_ids[doc.get("slug", "")] = (
                f"{jurisdiction}-{_slug(doc.get('slug', ''), jurisdiction)}"
            )

        for doc in data.get("new_documents") or []:
            errors = _check_quotes(texts, doc, "provisions")
            found = _find(texts, doc.get("source_url", ""))
            record = None
            if found is None:
                errors.append(f"source {doc.get('source_url')} is not one of the supplied texts")
            elif normalize_url(found[0]) in known:
                errors.append(f"{found[0]} is already a source of a record")
            if not errors:
                record = _ordered(
                    new_record(doc, page, texts, today=day, model=asettings.model), order
                )
                if record["id"] in existing_ids:
                    errors.append(f"{record['id']} already exists")
            if errors:
                pr.dropped.append(f"new document {doc.get('slug')}: " + "; ".join(errors))
                continue
            path = (
                paths.data
                / "documents"
                / jurisdiction
                / f"{record['id'].removeprefix(jurisdiction + '-')}.yaml"
            )
            existing_ids.add(record["id"])
            known.add(normalize_url(found[0]))
            contents[path] = record
            writes.append(_Write(path, None, pr, record["id"], True))

        for update in data.get("updates") or []:
            rec_id = update.get("id")
            errors = _check_quotes(texts, update, "add_provisions")
            if rec_id not in allowed_ids:
                errors.insert(0, "not one of the records given")
            if errors:
                pr.dropped.append(f"update to {rec_id}: " + "; ".join(errors))
                continue
            record = by_id[rec_id]
            path = record.path
            base_data = contents.get(path, record.data)
            contents[path] = _ordered(
                updated_record(base_data, update, page, texts, today=day, new_ids=new_ids), order
            )
            if not any(w.path == path for w in writes):
                writes.append(_Write(path, path.read_text(encoding="utf-8"), pr, rec_id, False))

    if writes and save:
        _write_all(writes, contents)
        # Put back every record that fails validation, until the rest pass; a record
        # put back can leave another pointing at it, so check again.
        live = list(writes)
        for _ in range(5):
            report, _ = validate(paths, today=today)
            bad = report.invalid_files
            failing = [w for w in live if w.path.relative_to(paths.root).as_posix() in bad]
            if not failing:
                break
            for w in failing:
                rel = w.path.relative_to(paths.root).as_posix()
                why = "; ".join(i.message for i in report.errors if i.file == rel)[:500]
                kind = "new document" if w.added else "update to"
                w.page.dropped.append(f"{kind} {w.record_id}: fails validation: {why}")
                _revert(w)
                live.remove(w)
        writes = live
    elif writes:
        log("not saving: the record changes are listed but not written")

    for w in writes:
        (w.page.added if w.added else w.page.updated).append(w.record_id)
    result.changed_files = (
        sorted(w.path.relative_to(paths.root).as_posix() for w in writes) if save else []
    )
    if save:
        for url, text in bases.items():
            state.write_base(url, text)
        state.save()
    return result


# -- reports -----------------------------------------------------------------------


def render_markdown(result: ApplyResult) -> str:
    """The pull request body: what changed on each page and what the records got."""
    acted = [p for p in result.pages if p.updated or p.added]
    noise = [p for p in result.pages if p.verdict in ("noise", "unchanged")]
    out = [
        "## Watched pages: records brought up to date",
        "",
        f"Run date {result.run_date}. {len(result.pages)} changed page(s) reviewed by "
        f"{result.model}: {len(acted)} led to record changes, {len(noise)} needed none.",
        "",
    ]
    for p in acted:
        out += [f"### <{p.url}>", "", p.what_changed, ""]
        out += [f"- Updated `{i}`" for i in p.updated] + [f"- Added `{i}`" for i in p.added]
        out.append("")
    review = render_review(result)
    if review:
        out += [review]
    return "\n".join(out).rstrip() + "\n"


def render_review(result: ApplyResult) -> str:
    """What a person should look at; empty when nothing needs it."""
    out: list[str] = []
    for p in result.pages:
        items = ([f"Not reviewed: {p.error}"] if p.error else []) + [
            f"Dropped: {d}" for d in p.dropped
        ]
        items += p.review
        if items:
            out += [f"### <{p.url}>", ""]
            if p.what_changed:
                out += [p.what_changed, ""]
            out += [f"- {i}" for i in items] + [""]
    if not out:
        return ""
    return "\n".join(["## Needs a person", "", *out]).rstrip() + "\n"
