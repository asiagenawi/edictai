"""Prepend a monthly entry to CHANGELOG.md.

Usage: changelog.py build/csv/records.csv CHANGELOG.md

The entry gives record counts by type from the build, and the record files
under data/ added or changed since the previous entry (from git history).
Writes `month=YYYY-MM` to $GITHUB_OUTPUT when it is set.
"""

import csv
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

HEADER = "# Changelog\n\nMonthly summary of the dataset. Counts come from the build.\n"
FOLDERS = ("legislation", "documents", "executive-actions", "research")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def changed_since(since: str) -> tuple[Counter, Counter]:
    added, modified = Counter(), Counter()
    out = git("log", f"--since={since}", "--name-status", "--format=", "--", "data/")
    for row in out.splitlines():
        parts = row.split("\t")
        if len(parts) < 2 or not parts[-1].endswith(".yaml"):
            continue
        folder = parts[-1].split("/")[1]
        if folder not in FOLDERS:
            continue
        (added if parts[0] == "A" else modified)[folder] += 1
    return added, modified


def main(records_csv: str, changelog: str) -> None:
    today = date.today()
    month = today.strftime("%Y-%m")
    with open(records_csv, newline="", encoding="utf-8") as f:
        counts = Counter(r["record_type"] for r in csv.DictReader(f))
    path = Path(changelog)
    text = path.read_text(encoding="utf-8") if path.exists() else HEADER
    if f"\n## {month}\n" in text:
        print(f"{month} already has an entry.")
        return
    previous = re.search(r"^## (\d{4}-\d{2})$", text, re.MULTILINE)
    since = f"{previous.group(1)}-01" if previous else today.replace(day=1).isoformat()
    added, modified = changed_since(since)
    lines = [f"## {month}", "", f"Records: {sum(counts.values())}"]
    lines += [f"- {kind}: {n}" for kind, n in sorted(counts.items())]
    lines += ["", f"Since {since}:"]
    for folder in FOLDERS:
        if added[folder] or modified[folder]:
            lines.append(f"- {folder}: {added[folder]} added, {modified[folder]} changed")
    if not (sum(added.values()) or sum(modified.values())):
        lines.append("- no record files changed")
    entry = "\n".join(lines) + "\n"
    head, _, rest = text.partition("\n## ")
    text = f"{head.rstrip()}\n\n{entry}" + (f"\n## {rest}" if rest else "")
    path.write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"month={month}\n")
    print(entry)


if __name__ == "__main__":
    main(*sys.argv[1:])
