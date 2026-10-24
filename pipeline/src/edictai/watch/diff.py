"""Unified diffs trimmed to the changed sections."""

from __future__ import annotations

import difflib
import re

_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _section_for(lines: list[str], index: int) -> str:
    """The nearest heading line at or above `index` (0-based) in `lines`."""
    for i in range(min(index, len(lines) - 1), -1, -1):
        if lines[i].startswith("#"):
            return lines[i].lstrip("#").strip()
    return ""


def diff_excerpt(
    old: str,
    new: str,
    *,
    context: int = 1,
    max_hunks: int = 8,
    max_lines: int = 80,
    max_line_chars: int = 300,
) -> str:
    """A unified diff of two normalized texts, labelled by section and capped in size.

    Each hunk header names the heading it falls under in the new text, so a
    reviewer can see where on the page the change sits without the full page.
    """
    old_lines, new_lines = old.splitlines(), new.splitlines()
    raw = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=context))
    if not raw:
        return ""
    out: list[str] = []
    hunks = 0
    total_hunks = sum(1 for line in raw if line.startswith("@@"))
    for line in raw[2:]:  # drop the ---/+++ file header
        m = _HUNK.match(line)
        if m:
            if hunks == max_hunks or len(out) >= max_lines:
                break
            hunks += 1
            section = _section_for(new_lines, int(m.group(2)) - 1)
            out.append(f"{m.group(0)} {section}".rstrip())
            continue
        if len(out) >= max_lines:
            continue
        if len(line) > max_line_chars:
            line = line[: max_line_chars - 1] + "…"
        out.append(line)
    if hunks < total_hunks or len(out) >= max_lines:
        out.append(f"… diff trimmed ({total_hunks} hunk(s) in full)")
    return "\n".join(out)
