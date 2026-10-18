"""List candidates that are not yet records and were not listed by an earlier run.

Usage: new_candidates.py CANDIDATES_DIR REPORT.md

Reads the newest state file (YYYY-MM-DD.jsonl) and the newest federal file
(federal-YYYY-MM-DD.jsonl), skips rows that already match a record, and
remembers what it has listed in CANDIDATES_DIR/seen.json so each candidate
appears in one report only. Writes an empty report when nothing is new.
"""

import json
import sys
from pathlib import Path

LIMIT = 300


def newest(folder: Path, pattern: str) -> Path | None:
    files = sorted(p for p in folder.glob(pattern) if not p.name.endswith(".summary.json"))
    return files[-1] if files else None


def rows(path: Path | None):
    if path is None:
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def key(row: dict) -> str:
    if row.get("source") == "federal_register":
        return f"fr:{row['document_number']}"
    if row.get("source") == "congress":
        return row["record_id"]
    return f"{row['jurisdiction']}:{row['session']}:{row['identifier']}"


def line(row: dict) -> str:
    title = (row.get("title") or "").replace("|", "/")
    if row.get("source") == "federal_register":
        label = (
            f"EO {row['executive_order_number']}"
            if row.get("executive_order_number")
            else row["document_number"]
        )
        return f"| us | {label} | {title} | {row.get('publication_date') or ''} | {row.get('html_url') or ''} |"
    if row.get("source") == "congress":
        return f"| us | {row['identifier']} | {title} | {row.get('introduced_date') or ''} | {row.get('url') or ''} |"
    return f"| {row['jurisdiction']} | {row['identifier']} | {title} | {row.get('status_date') or ''} | {row.get('url') or ''} |"


def main(folder: str, report: str) -> None:
    root = Path(folder)
    seen_path = root / "seen.json"
    seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()
    first_run = not seen_path.exists()
    fresh = []
    for path in (newest(root, "[0-9]*.jsonl"), newest(root, "federal-*.jsonl")):
        for row in rows(path):
            if row.get("existing_record"):
                continue
            k = key(row)
            if k not in seen:
                seen.add(k)
                fresh.append(row)
    seen_path.write_text(json.dumps(sorted(seen)) + "\n")
    out = Path(report)
    # The first run only records what exists, so the backlog isn't filed as one issue.
    if first_run or not fresh:
        out.write_text("")
        print("Baseline recorded." if first_run else "Nothing new.")
        return
    body = [
        f"{len(fresh)} new candidates since the last report. Each needs classification "
        "before it can become a record.",
        "",
        "| Jurisdiction | Bill or document | Title | Date | Link |",
        "|---|---|---|---|---|",
        *(line(r) for r in fresh[:LIMIT]),
    ]
    if len(fresh) > LIMIT:
        body += ["", f"…and {len(fresh) - LIMIT} more in the candidate files."]
    out.write_text("\n".join(body) + "\n")
    print(f"{len(fresh)} new candidates.")


if __name__ == "__main__":
    main(*sys.argv[1:])
