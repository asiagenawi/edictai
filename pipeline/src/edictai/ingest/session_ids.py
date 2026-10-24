"""Map LegiScan sessions to the session identifiers used in data/.

- Regular annual session: the year, "2025".
- Regular session spanning two years: the range, "2025-2026".
- Congress: the Congress number, "119".
- Special session: the year it convened plus an ordinal suffix, "2025-s1".
  The ordinal comes from the session name ("1st Special Session",
  "Second Extraordinary Session"); when the name has none, specials in the
  same state and year are numbered in LegiScan session-id order.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

FIRST_CONGRESS_YEAR = 1789

_ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}
_ORD = r"(?:(\d+)(?:st|nd|rd|th)|(" + "|".join(_ORDINAL_WORDS) + r"))"
# "1st Special Session", "Second Extraordinary Session", "Special Session 2"
_BEFORE = re.compile(_ORD + r"\s+(?:special|extraordinary)\b", re.IGNORECASE)
_AFTER = re.compile(r"\b(?:special|extraordinary)\s+session\s+#?(\d+)\b", re.IGNORECASE)


def congress_number(year_start: int) -> int:
    return (year_start - FIRST_CONGRESS_YEAR) // 2 + 1


def special_ordinal(*names: str | None) -> int | None:
    """The ordinal in a special session's name, or None."""
    for name in names:
        if not name:
            continue
        m = _BEFORE.search(name)
        if m:
            return int(m.group(1)) if m.group(1) else _ORDINAL_WORDS[m.group(2).lower()]
        m = _AFTER.search(name)
        if m:
            return int(m.group(1))
    return None


def base_identifier(state: str, year_start: int, year_end: int) -> str:
    if state.upper() == "US":
        return str(congress_number(year_start))
    if year_end and year_end != year_start:
        return f"{year_start}-{year_end}"
    return str(year_start)


def assign_identifiers(sessions: Iterable[Mapping[str, Any]]) -> dict[int, str]:
    """session_id -> identifier for LegiScan session records.

    Each record needs state (abbreviation), session_id, year_start, year_end,
    special, and optionally session_tag / session_name / session_title.
    """
    result: dict[int, str] = {}
    specials: dict[tuple[str, int], list[Mapping[str, Any]]] = {}
    for s in sessions:
        state = str(s["state"]).upper()
        if s.get("special"):
            specials.setdefault((state, int(s["year_start"])), []).append(s)
        else:
            result[int(s["session_id"])] = base_identifier(
                state, int(s["year_start"]), int(s.get("year_end") or s["year_start"])
            )
    for (_state, year), group in specials.items():
        group = sorted(group, key=lambda s: int(s["session_id"]))
        used: set[int] = set()
        pending: list[Mapping[str, Any]] = []
        for s in group:
            n = special_ordinal(s.get("session_tag"), s.get("session_name"), s.get("session_title"))
            if n is None or n in used:
                pending.append(s)
                continue
            used.add(n)
            result[int(s["session_id"])] = f"{year}-s{n}"
        n = 0
        for s in pending:
            n += 1
            while n in used:
                n += 1
            used.add(n)
            result[int(s["session_id"])] = f"{year}-s{n}"
    return result


def align(identifier: str, listed: Iterable[str]) -> str:
    """Prefer a session listed in data/jurisdictions over LegiScan's split of it.

    LegiScan splits some two-year legislatures into annual sessions (Hawaii,
    Oklahoma). When the jurisdiction file lists a two-year session that
    contains LegiScan's year, the listed identifier is used.
    """
    listed = list(listed)
    if identifier in listed or not re.fullmatch(r"\d{4}", identifier):
        return identifier
    year = int(identifier)
    for other in listed:
        m = re.fullmatch(r"(\d{4})-(\d{4})", other)
        if m and int(m.group(1)) <= year <= int(m.group(2)):
            return other
    return identifier
