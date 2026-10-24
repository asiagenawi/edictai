"""The watch report and its Markdown rendering for a pull request body."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ChangeEntry:
    org_id: str
    url: str
    observed: str  # ISO date
    old_hash: str | None
    new_hash: str
    diff_excerpt: str
    ai_links: list[dict[str, str]] = field(default_factory=list)
    hash_basis: str = "text"  # "text" or "bytes"
    content_kind: str = "html"
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WatchReport:
    run_date: str
    since: str | None = None
    checked: int = 0
    unchanged: list[dict[str, Any]] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)  # ChangeEntry dicts
    baselined: list[dict[str, Any]] = field(default_factory=list)  # first observations
    skipped: list[dict[str, Any]] = field(default_factory=list)  # robots, blocks
    errors: list[dict[str, Any]] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)  # RevisionProposal dicts

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _short(h: str | None) -> str:
    return f"`{h[:12]}`" if h else "—"


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]").replace("\n", " ")


def _longest_backtick_run(text: str) -> int:
    runs = re.findall(r"`+", text)
    return max((len(r) for r in runs), default=0)


def render_markdown(report: WatchReport) -> str:
    lines: list[str] = []
    n = len(report.changes)
    lines.append(f"## Watched pages: {n} changed" if n else "## Watched pages: no changes")
    lines.append("")
    scope = f"Changes observed since {report.since}" if report.since else "Changes from this run"
    lines.append(f"Run date {report.run_date}. {scope}.")
    lines.append("")
    lines.append("| Checked | Unchanged | Changed | First observation | Skipped | Errors |")
    lines.append("| ---: | ---: | ---: | ---: | ---: | ---: |")
    lines.append(
        f"| {report.checked} | {len(report.unchanged)} | {n} | {len(report.baselined)} "
        f"| {len(report.skipped)} | {len(report.errors)} |"
    )
    lines.append("")

    proposals_by_url: dict[str, list[dict[str, Any]]] = {}
    for p in report.proposals:
        proposals_by_url.setdefault(p["revision"]["url"], []).append(p)

    for change in report.changes:
        title = f" — {_md_escape(change['title'])}" if change.get("title") else ""
        lines.append(f"### `{change['org_id']}`{title}")
        lines.append("")
        lines.append(f"<{change['url']}>")
        lines.append("")
        basis = " (file bytes)" if change.get("hash_basis") == "bytes" else ""
        lines.append(
            f"Observed {change['observed']}: {_short(change['old_hash'])} → "
            f"{_short(change['new_hash'])}{basis}"
        )
        lines.append("")
        if change.get("diff_excerpt"):
            lines.append("<details><summary>Diff</summary>")
            lines.append("")
            fence = "`" * max(3, _longest_backtick_run(change["diff_excerpt"]) + 1)
            lines.append(f"{fence}diff")
            lines.append(change["diff_excerpt"])
            lines.append(fence)
            lines.append("")
            lines.append("</details>")
            lines.append("")
        if change.get("ai_links"):
            lines.append("New links that look like AI documents:")
            lines.append("")
            for link in change["ai_links"]:
                text = _md_escape(link.get("text") or link["url"])
                lines.append(f"- [{text}]({link['url']})")
            lines.append("")
        for p in proposals_by_url.get(change["url"], []):
            lines.append(
                f"Proposed revision for `{p['document_id']}` (`data/{p['path']}`, matched by {p['via']}):"
            )
            lines.append("")
            lines.append("```yaml")
            rev = p["revision"]
            lines.append(f"- observed: '{rev['observed']}'")
            lines.append(f"  url: {rev['url']}")
            if rev.get("sha256"):
                lines.append(f"  sha256: {rev['sha256']}")
            lines.append(f"  note: {rev['note']}")
            lines.append("```")
            lines.append("")

    if report.baselined:
        with_links = [b for b in report.baselined if b.get("ai_links")]
        lines.append(
            f"First observation recorded for {len(report.baselined)} page(s); "
            "they will be compared on the next run."
        )
        lines.append("")
        if with_links:
            lines.append(
                "<details><summary>AI-looking links on newly watched pages that no "
                "document record cites</summary>"
            )
            lines.append("")
            for b in with_links:
                lines.append(f"- `{b['org_id']}` <{b['url']}>")
                for link in b["ai_links"]:
                    lines.append(
                        f"  - [{_md_escape(link.get('text') or link['url'])}]({link['url']})"
                    )
            lines.append("")
            lines.append("</details>")
            lines.append("")

    for label, items in (("Skipped", report.skipped), ("Errors", report.errors)):
        if items:
            lines.append(f"<details><summary>{label} ({len(items)})</summary>")
            lines.append("")
            for item in items:
                lines.append(f"- `{item['org_id']}` <{item['url']}>: {item.get('reason', '')}")
            lines.append("")
            lines.append("</details>")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
