"""`classify summary`: the weekly run's pull-request body and result file.

Reads what the run's steps wrote (the `classify run`, `classify refresh`,
`classify eval-gate`, and `articles write` JSON files) and the record files git
reports as added or changed under data/, and writes:

- a Markdown summary for people: records added, records updated, items ruled
  out, items dropped, the week's article (or why there is none), spending, and
  the evaluation;
- a JSON result with the same facts, for a later step to act on.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import Paths
from ..yamlio import load_yaml

RECORD_DIRS = ("legislation", "documents", "executive-actions")
BODY_MAX = 60000  # GitHub limits a pull-request body to 65,536 characters


def _read(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def git_changes(root: Path) -> tuple[list[str], list[str]]:
    """(added, modified) paths under data/, relative to the repository."""
    out = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "data/"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    added, modified = [], []
    for line in out.splitlines():
        code, name = line[:2], line[3:].strip()
        if " -> " in name:
            name = name.split(" -> ", 1)[1]
        if "?" in code or "A" in code:
            added.append(name)
        elif "M" in code:
            modified.append(name)
    return sorted(added), sorted(modified)


@dataclass
class Summary:
    status: str
    added: list[dict[str, Any]] = field(default_factory=list)
    updated: list[dict[str, Any]] = field(default_factory=list)
    other_changes: list[str] = field(default_factory=list)
    ruled_out: list[dict[str, Any]] = field(default_factory=list)
    triaged_out: int = 0
    dropped: list[dict[str, Any]] = field(default_factory=list)
    not_sent: int = 0
    spend: dict[str, Any] = field(default_factory=dict)
    eval: dict[str, Any] = field(default_factory=dict)
    failed_step: str | None = None
    # `articles write --result`; None when the run had no article step
    article: dict[str, Any] | None = None

    @property
    def article_written(self) -> bool:
        return bool(self.article and self.article.get("status") == "written")

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.other_changes or self.article_written)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "failed_step": self.failed_step,
            "changed": self.changed,
            "added": self.added,
            "updated": self.updated,
            "other_changes": self.other_changes,
            "ruled_out": self.ruled_out,
            "triaged_out": self.triaged_out,
            "dropped": self.dropped,
            "not_sent": self.not_sent,
            "spend": self.spend,
            "eval": self.eval,
            "article": self.article,
        }


def _judgments(run: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for j in run.get("judgments") or []:
        # A full judgment replaces the triage one for the same key.
        if j.get("stage") == "full" or j["key"] not in out:
            out[j["key"]] = j
    return out


def _first_url(record: Mapping[str, Any]) -> str | None:
    for s in record.get("sources") or []:
        if isinstance(s, Mapping) and s.get("url"):
            return str(s["url"])
    return None


def build_summary(
    paths: Paths,
    *,
    run: Path | None = None,
    refresh: Path | None = None,
    gate: Path | None = None,
    article: Path | None = None,
    status: str = "success",
    failed_step: str | None = None,
    changes: tuple[list[str], list[str]] | None = None,
) -> Summary:
    run_data, refresh_data, gate_data = _read(run), _read(refresh), _read(gate)
    s = Summary(status=status, failed_step=failed_step)
    judged = _judgments(run_data)
    added, modified = changes if changes is not None else git_changes(paths.root)
    refreshed = {u["path"]: u for u in refresh_data.get("updated") or []}
    for rel in added:
        parts = Path(rel).parts
        if len(parts) < 2 or parts[1] not in RECORD_DIRS or not rel.endswith(".yaml"):
            s.other_changes.append(rel)
            continue
        record = load_yaml(paths.root / rel) or {}
        rid = str(record.get("id") or Path(rel).stem)
        j = judged.get(rid, {})
        s.added.append(
            {
                "id": rid,
                "path": rel,
                "title": record.get("title"),
                "identifier": record.get("identifier") or record.get("number"),
                "confidence": j.get("confidence"),
                "verification": (record.get("verification") or {}).get("status"),
                "url": _first_url(record),
            }
        )
    for rel in modified:
        if rel in refreshed:
            u = refreshed[rel]
            record = load_yaml(paths.root / rel) or {}
            s.updated.append(
                {
                    "id": u["id"],
                    "path": rel,
                    "title": record.get("title"),
                    "status_before": u.get("status_before"),
                    "status_after": u.get("status_after"),
                    "added_actions": [
                        f"{a['date']}: {a['description']}" for a in u.get("added_actions") or []
                    ],
                    "added_versions": len(u.get("added_versions") or []),
                    "url": _first_url(record),
                }
            )
        else:
            s.other_changes.append(rel)
    for j in judged.values():
        if j.get("stage") == "full" and j.get("in_scope") is False:
            s.ruled_out.append(
                {k: j.get(k) for k in ("key", "title", "confidence", "reasons", "url", "workdir")}
            )
        elif j.get("stage") == "triage" and j.get("decision") == "out":
            s.triaged_out += 1
    for part in ("state", "federal"):
        w = run_data.get(part) or {}
        for stage in ("triage", "full"):
            st = w.get(stage) or {}
            s.dropped += [{**d, "stage": stage, "workdir": part} for d in st.get("dropped") or []]
            s.not_sent += len(st.get("not_sent") or [])
    s.spend = run_data.get("spend") or {}
    if article is not None:
        s.article = _read(article) or {"status": "missing"}
    s.eval = {
        k: gate_data.get(k)
        for k in ("passed", "ran", "precision", "recall", "matched", "model", "effort", "reasons")
        if k in gate_data
    }
    return s


def _cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ")


def _link(text: Any, url: Any) -> str:
    return f"[{_cell(text)}]({url})" if url else _cell(text)


def _article_lines(a: Mapping[str, Any]) -> list[str]:
    out = ["### Article", ""]
    status = a.get("status")
    if status == "missing":
        return [*out, "The article step did not finish; the run's log has the details.", ""]
    if status not in ("written", "exists"):
        out.append(f"No article this week: {_cell(a.get('reason'))}")
        subject = a.get("subject") or {}
        if subject.get("key"):
            out.append(f"(subject tried: {subject.get('type')}, `{subject['key']}`)")
        return [*out, ""]
    c = a.get("citations") or {}
    recs, studies = c.get("records") or [], c.get("studies") or []
    cites = f"{c.get('notes', 0)} notes citing {len(recs)} records" + (
        f" and {len(studies)} studies" if studies else ""
    )
    out += [
        f"**{_cell(a.get('title'))}** ({_cell(a.get('type'))}, `{a.get('path')}`)",
        "",
        f"> {_cell(a.get('dek'))}",
        "",
        f"{a.get('words')} words; {cites}. Subject: `{(a.get('subject') or {}).get('key')}`.",
    ]
    if status == "exists":
        out.append("Written earlier today and restored; nothing was sent this run.")
    spend = a.get("spend") or {}
    if spend:
        if spend.get("unit") == "calls":
            out.append(
                f"Article spending: {spend.get('calls', 0)} of its {spend.get('cap_calls', 0)} "
                f"Claude Code calls ({a.get('model')})."
            )
        else:
            out.append(
                f"Article spending: ${spend.get('spent_usd', 0):.2f} of its "
                f"${spend.get('cap_usd', 0):.2f} cap ({a.get('model')})."
            )
    out.append("")
    edits = a.get("edits") or []
    if edits:
        out += ["<details><summary>Editor's changes</summary>", ""]
        out += [f"- {_cell(e)}" for e in edits]
        out += ["", "</details>", ""]
    if recs or studies:
        out += ["<details><summary>Cited records and studies</summary>", ""]
        out += [f"- `{i}`" for i in recs + studies]
        out += ["", "</details>", ""]
    return out


def markdown(s: Summary) -> str:
    out: list[str] = []
    if s.status != "success":
        out += [
            f"The weekly update failed at **{s.failed_step or 'an unknown step'}**. "
            "Nothing was merged. The run's log has the details.",
            "",
        ]
    out.append(
        f"Records added: {len(s.added)}. Records updated: {len(s.updated)}. "
        f"Ruled out after reading the text: {len(s.ruled_out)}. "
        f"Ruled out at triage: {s.triaged_out}."
    )
    out.append("")
    if s.added:
        out += [
            "### Added",
            "",
            "| Record | Title | Confidence | Verification |",
            "|---|---|---|---|",
        ]
        out += [
            f"| {_link(a['id'], a['url'])} | {_cell(a['title'])} | {_cell(a['confidence'])} "
            f"| {_cell(a['verification'])} |"
            for a in s.added
        ]
        out.append("")
    if s.updated:
        out += ["### Updated", ""]
        for u in s.updated:
            status = (
                f"{u['status_before']} to {u['status_after']}"
                if u["status_before"] != u["status_after"]
                else f"still {u['status_after']}"
            )
            out.append(f"- {_link(u['id'], u['url'])} ({status})")
            out += [f"  - {_cell(a)}" for a in u["added_actions"]]
            if u["added_versions"]:
                out.append(f"  - {u['added_versions']} new text version(s)")
        out.append("")
    if s.ruled_out:
        out += [
            "### Ruled out",
            "",
            "<details><summary>Judged out of scope after reading the text</summary>",
            "",
            "| Item | Confidence | Reason |",
            "|---|---|---|",
        ]
        out += [
            f"| {_link(r['key'], r['url'])} | {_cell(r['confidence'])} | {_cell(r['reasons'])} |"
            for r in s.ruled_out
        ]
        out += ["", "</details>", ""]
    if s.dropped:
        out += ["### Dropped", "", "These failed the checks twice and were left out:", ""]
        out += [
            f"- {d['key']} ({d['stage']}): {_cell('; '.join(d.get('errors') or []))}"
            for d in s.dropped
        ]
        out.append("")
    if s.not_sent:
        out += [
            f"{s.not_sent} items were not sent within the spending cap; the next run takes them.",
            "",
        ]
    if s.article is not None:
        out += _article_lines(s.article)
    if s.other_changes:
        out += ["### Other changed files", ""] + [f"- `{p}`" for p in s.other_changes] + [""]
    if s.spend and s.spend.get("unit") == "calls":
        out.append(
            f"Spending: {s.spend.get('calls', 0)} of the {s.spend.get('cap_calls', 0)} Claude "
            f"Code calls, ${s.spend.get('usd_equivalent', 0):.2f} at API prices "
            f"({s.spend.get('model')}, effort {s.spend.get('effort')})."
        )
    elif s.spend:
        out.append(
            f"Spending: ${s.spend.get('spent_usd', 0):.2f} of the "
            f"${s.spend.get('cap_usd', 0):.2f} cap, {s.spend.get('requests', 0)} requests "
            f"({s.spend.get('model')}, effort {s.spend.get('effort')})."
        )
    if s.eval:
        ran = "run this week" if s.eval.get("ran") else "stored result for these instructions"
        out.append(
            f"Evaluation ({ran}): precision {s.eval.get('precision')}, recall "
            f"{s.eval.get('recall')} on {s.eval.get('matched')} entries."
        )
    body = "\n".join(out).rstrip() + "\n"
    if len(body) > BODY_MAX:
        body = (
            body[: BODY_MAX - 80].rsplit("\n", 1)[0]
            + "\n\n(cut for length; see weekly-result.json)\n"
        )
    return body
