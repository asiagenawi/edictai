"""Normalise bill numbers so LegiScan rows can be matched to data/legislation records.

LegiScan writes numbers without spaces and sometimes zero-padded ("HB0168"),
uses its own prefixes for Congress ("HB9211" for H.R. 9211), and writes some
state prefixes its own way ("S1194" for Florida SB 1194, "HJR32" for
Virginia HJ 32). Records use the number
as the legislature writes it.
"""

from __future__ import annotations

import re

# LegiScan prefix -> official prefix for Congress.
_US_PREFIXES = {
    "HB": "H.R.",
    "SB": "S.",
    "HR": "H.Res.",
    "SR": "S.Res.",
    "HJR": "H.J.Res.",
    "SJR": "S.J.Res.",
    "HCR": "H.Con.Res.",
    "SCR": "S.Con.Res.",
}

# State prefixes LegiScan writes differently from the legislature.
_STATE_PREFIXES = {
    "us-fl": {"H": "HB", "S": "SB"},
    "us-va": {"HJR": "HJ", "SJR": "SJ"},
}

_NUMBER = re.compile(r"\s*([A-Za-z][A-Za-z.\s]*?)\s*0*(\d+)([A-Za-z]?)\s*$")


def normalize(jurisdiction: str, number: str) -> str:
    """The bill number as the legislature writes it: 'HB0168' -> 'HB 168'.

    For Congress the LegiScan prefix becomes the official one ('HB9211' ->
    'H.R. 9211'); an official prefix with periods is kept. Numbers that do not look like prefix + digits come back
    unchanged apart from surrounding whitespace.
    """
    m = _NUMBER.fullmatch(number)
    if not m:
        return number.strip()
    prefix = re.sub(r"[.\s]", "", m.group(1)).upper()
    digits = str(int(m.group(2))) + m.group(3).upper()
    if jurisdiction == "us":
        if "." in m.group(1):  # already official: H.R., S.J.Res.
            official = re.sub(r"\s", "", m.group(1))
            return f"{official} {digits}"
        official = _US_PREFIXES.get(prefix)
        if official:
            return f"{official} {digits}"
        return f"{m.group(1).strip()} {digits}"
    prefix = _STATE_PREFIXES.get(jurisdiction, {}).get(prefix, prefix)
    return f"{prefix} {digits}"


# Washington marks substitutes and engrossments in the prefix: E2SSB 5838 is SB 5838.
_WA_MARKERS = re.compile(r"^(?:e?\d?s|e)(?=(hb|sb|hjm|sjm|hjr|sjr|hcr|scr)$)")


def match_key(jurisdiction: str, number: str) -> tuple[str, int] | None:
    """(prefix letters, number) for comparing a record identifier with a LegiScan number.

    'H.R. 610' and LegiScan 'HB610' both give ('hr', 610) for Congress; a
    Colorado 'SB 25-288' gives ('sb', 288), the per-session number; version
    suffixes such as New York's 'A 6874-A' are ignored.
    """
    m = re.fullmatch(
        r"\s*([A-Za-z][A-Za-z0-9.\s]*?)\s*(?:\d{2}-)?0*(\d+)(?:-?[A-Za-z])?\s*", number
    )
    if not m:
        return None
    prefix = re.sub(r"[^a-z0-9]", "", m.group(1).lower())
    if jurisdiction == "us-wa":
        prefix = _WA_MARKERS.sub("", prefix)
    return prefix, int(m.group(2))


def legiscan_key(jurisdiction: str, number: str) -> tuple[str, int] | None:
    """match_key for a LegiScan bill number, after normalising its prefix."""
    return match_key(jurisdiction, normalize(jurisdiction, number))
