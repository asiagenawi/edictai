"""Entry point: check every organization's watch_urls and report changes."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from typing import Any

from ..paths import Paths
from ..records import Record, load_records
from .diff import diff_excerpt
from .extract import extract, looks_like_ai_link
from .fetch import Fetcher, FetchResult, Transport, host_key, urllib_transport
from .mapping import known_urls, normalize_url, propose_revisions
from .report import ChangeEntry, WatchReport, render_markdown
from .state import WatchState


def watch_targets(
    organizations: Iterable[Record], org_ids: Iterable[str] | None = None
) -> list[tuple[str, str]]:
    """(org_id, url) for every watch URL, in record order, without duplicates."""
    wanted = set(org_ids) if org_ids else None
    seen: set[str] = set()
    targets: list[tuple[str, str]] = []
    for record in organizations:
        org_id = record.id or ""
        if wanted is not None and org_id not in wanted:
            continue
        for url in record.data.get("watch_urls") or []:
            if isinstance(url, str) and url not in seen:
                seen.add(url)
                targets.append((org_id, url))
    return targets


def _ai_links(links: list[dict[str, str]], exclude: set[str]) -> list[dict[str, str]]:
    return [
        link
        for link in links
        if looks_like_ai_link(link["url"], link.get("text", ""))
        and normalize_url(link["url"]) not in exclude
    ]


def run(
    paths: Paths,
    since: date | str | None = None,
    *,
    org_ids: Iterable[str] | None = None,
    urls: Iterable[str] | None = None,
    limit: int | None = None,
    transport: Transport | None = None,
    today: date | None = None,
    cache_dir: Path | None = None,
    save: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], float] = time.time,
) -> tuple[WatchReport, str]:
    """Check the watch URLs and return (report, Markdown summary).

    `since` widens the reported changes to every change logged in the state
    on or after that date, so one pull request can gather several runs; by
    default only this run's changes are reported. `org_ids`, `urls` and
    `limit` narrow which pages are fetched. Nothing under data/ is written;
    state goes to `cache_dir` (default .cache/watch/).
    """
    today = today or date.today()
    run_date = today.isoformat()
    since_s = since.isoformat() if isinstance(since, date) else since
    records = load_records(paths.data, ["organization", "document"])
    documents = records.of("document")
    known = known_urls(documents)

    targets = watch_targets(records.of("organization"), org_ids)
    if urls is not None:
        wanted = {normalize_url(u) for u in urls}
        targets = [t for t in targets if normalize_url(t[1]) in wanted]
    if limit is not None:
        targets = targets[:limit]

    state = WatchState.load(cache_dir or paths.cache / "watch")
    # Least recently fetched first, so a page deferred by a host's crawl delay
    # gets its turn on the next run.
    targets.sort(key=lambda t: state.urls.get(t[1], {}).get("last_fetched") or "")
    fetcher = Fetcher(transport or urllib_transport(), sleep=sleep, clock=clock)
    report = WatchReport(run_date=run_date, since=since_s)
    run_changes: list[dict[str, Any]] = []

    for org_id, url in targets:
        report.checked += 1
        entry = state.entry(url)
        entry["org_id"] = org_id
        old_text = state.read_text(url)
        old_links = state.read_links(url)
        have_snapshot = old_links is not None and entry.get("sha256")
        host = host_key(url)
        seen_host = state.hosts.get(host, {})
        delay = seen_host.get("crawl_delay", 0)
        if delay and wall() - seen_host.get("last_page_at", 0) < delay:
            result = FetchResult(
                url, "deferred", error=f"robots.txt Crawl-delay {delay:g}s; left for the next run"
            )
        else:
            before = fetcher.host_info(url).last_page
            result = fetcher.get(
                url,
                etag=entry.get("etag") if have_snapshot else None,
                last_modified=entry.get("last_modified") if have_snapshot else None,
            )
            info = fetcher.host_info(url)
            if info.last_page is not None and info.last_page != before:
                state.hosts[host] = {"last_page_at": wall(), "crawl_delay": info.crawl_delay}
        entry["last_checked"] = run_date
        if result.outcome != "deferred":
            entry["last_fetched"] = run_date
        base = {"org_id": org_id, "url": url}

        if result.outcome in ("robots-disallowed", "robots-unavailable", "blocked", "deferred"):
            entry["last_outcome"] = result.outcome
            report.skipped.append({**base, "reason": result.error or result.outcome})
            continue
        if result.outcome == "error":
            entry["last_outcome"] = "error"
            report.errors.append({**base, "reason": result.error})
            continue
        if result.outcome == "not-modified":
            entry["last_outcome"] = "not-modified"
            report.unchanged.append({**base, "sha256": entry.get("sha256"), "via": "304"})
            continue

        extracted = extract(result.body, result.content_type, result.final_url or url)
        if extracted.basis == "text" and not extracted.text:
            entry["last_outcome"] = "error"
            report.errors.append(
                {**base, "reason": "no text extracted (the page may be rendered by JavaScript)"}
            )
            continue
        links = [link.to_dict() for link in extracted.links]
        entry.update(
            etag=result.etag,
            last_modified=result.last_modified,
            content_type=result.content_type,
            hash_basis=extracted.basis,
            last_outcome="ok",
        )
        if result.final_url and result.final_url != url:
            entry["final_url"] = result.final_url
        old_hash = entry.get("sha256")

        if old_hash == extracted.sha256:
            report.unchanged.append({**base, "sha256": old_hash, "via": "hash"})
            state.write_snapshot(url, extracted.text, links)
            continue

        entry["sha256"] = extracted.sha256
        entry["history"].append({"observed": run_date, "sha256": extracted.sha256})
        state.write_snapshot(url, extracted.text, links)

        if old_hash is None:
            report.baselined.append(
                {
                    **base,
                    "sha256": extracted.sha256,
                    "ai_links": _ai_links(links, known | {normalize_url(url)}),
                }
            )
            continue

        previous = {normalize_url(link["url"]) for link in old_links or []}
        change = ChangeEntry(
            org_id=org_id,
            url=url,
            observed=run_date,
            old_hash=old_hash,
            new_hash=extracted.sha256,
            diff_excerpt=(
                diff_excerpt(old_text, extracted.text)
                if old_text is not None and extracted.text is not None
                else ""
            ),
            ai_links=_ai_links(links, known | previous | {normalize_url(url)}),
            hash_basis=extracted.basis,
            content_kind=extracted.kind,
            title=extracted.title,
        ).to_dict()
        run_changes.append(change)
        state.changes.append(change)

    if since_s:
        seen = set()
        for change in state.changes:
            key = (change["url"], change["new_hash"])
            if change["observed"] >= since_s and key not in seen:
                seen.add(key)
                report.changes.append(change)
    else:
        report.changes = run_changes

    for change in report.changes:
        report.proposals.extend(p.to_dict() for p in propose_revisions(change, documents))

    if save:
        state.save()
    return report, render_markdown(report)
