"""Map Congress.gov bill actions to the `action_classifications` vocabulary.

Congress.gov reports each action with an `actionCode` (Library of Congress and
House codes; Senate actions usually have none), an action `type`, the
`sourceSystem` that recorded it, and free text. Classification looks at the
code first and the text second:

- `READINGS` add reading-1/2/3 wherever the text records a reading, so
  "Read twice and referred to the Committee on ..." becomes
  `[reading-2, referral-committee]`.
- `RULES` are tried in order and the first match supplies the main class.
- Anything unmatched is `other`.

Congress.gov often lists one event twice (once from the chamber, once from
the Library of Congress). Both are kept; deduplication belongs to whoever
writes the record.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Rule:
    classification: tuple[str, ...]
    codes: frozenset[str] = field(default_factory=frozenset)
    pattern: re.Pattern[str] | None = None

    def matches(self, code: str | None, text: str) -> bool:
        if code and code in self.codes:
            return True
        return bool(self.pattern and self.pattern.search(text))


def _rule(classes: str, codes: str = "", pattern: str | None = None) -> Rule:
    return Rule(
        tuple(classes.split()),
        frozenset(codes.split()),
        re.compile(pattern, re.IGNORECASE) if pattern else None,
    )


READINGS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("reading-1", re.compile(r"\bread (?:the )?first time\b", re.IGNORECASE)),
    ("reading-2", re.compile(r"\bread (?:twice|the second time)\b", re.IGNORECASE)),
    ("reading-3", re.compile(r"\bread (?:the )?third time\b", re.IGNORECASE)),
)

# Order matters: the first matching rule wins.
RULES: tuple[Rule, ...] = (
    # Executive and enactment.
    _rule("became-law", "E40000", r"^Became (?:Public|Private) Law\b"),
    _rule("executive-signature", "E30000", r"^Signed by (?:the )?President\b"),
    _rule("executive-veto", "31000 E50000", r"\b(?:Pocket )?Vetoed by (?:the )?President\b"),
    # Presentment counts as enrollment in this vocabulary.
    _rule("enrollment", "28000 E20000", r"^Presented to (?:the )?President\b"),
    _rule(
        "veto-override-passage",
        "",
        r"\bpassed (?:House|Senate) over veto\b|objections of the President to the contrary "
        r"notwithstanding,? (?:Passed|Agreed)",
    ),
    _rule(
        "enrollment",
        "",
        r"\benrolled (?:bill|measure)\b|^Signed by (?:the )?(?:Speaker|President pro tempore|"
        r"Vice President)\b|^Enrolled\b",
    ),
    # Differences between the chambers.
    _rule(
        "conference-committee",
        "19500 20500 21000 22000 23000 H25200 H40150 H41800",
        r"\bconferees?\b|\bconference report\b|\bconference held\b|\brequest(?:s|ed)? a conference\b",
    ),
    # Special rules (H. Res. "rule" measures) are recorded on the bill they govern.
    _rule("other", "H1L210 H1L220", r"^Rule H\. ?Res\. ?\d+|^Rules Committee Resolution\b"),
    # Passage and failure of the measure itself.
    _rule("failure", "9000 18000", r"\bfailed of passage\b|\bon passage failed\b"),
    _rule(
        "passage",
        "8000 17000 H37100 H37300",
        r"^Passed/agreed to in (?:House|Senate)\b|\bPassed (?:House|Senate)\b|"
        r"\bon passage passed\b|\bsuspend the rules and (?:pass|agree to) the "
        r"(?:bill|resolution)[^.]*Agreed to\b|\b(?:Resolution|Concurrent resolution|Joint "
        r"resolution) agreed to in (?:House|Senate)\b|\bagreed to (?:in|by) (?:the )?Senate "
        r"(?:with|without) (?:an )?amendment|\bsubmitted in the Senate,? (?:considered,? )?and "
        r"agreed to\b",
    ),
    _rule(
        "amendment-passage",
        "",
        r"\bamendment (?:SA|S\.Amdt\.|H\.Amdt\.) ?\d+[^.]*\b(?:agreed to|adopted)\b|"
        r"\bon agreeing to the [^.]*amendments?[^.]* Agreed to\b|\badopted the amendments en "
        r"gross?\b",
    ),
    _rule("withdrawal", "", r"\bwithdrawn by (?:the )?sponsor\b|\bmeasure withdrawn\b"),
    # Committee stage.
    _rule("committee-hearing", "", r"\bhearings? held\b"),
    _rule(
        "committee-passage",
        "5000 H12200 H19000 H23000",
        r"\bordered to be reported\b|^Reported (?:\(Amended\) )?by\b|\breported (?:by|with|"
        r"without) (?:Senator|Mr\.|Ms\.|Mrs\.|an amendment|amendments?)\b|\bforwarded by "
        r"subcommittee to full committee\b",
    ),
    _rule(
        "referral-committee",
        "H11000 H11100",
        r"\breferred to (?:the )?(?:House |Senate )?(?:Committee|Subcommittee|Committees)\b|"
        r"\breferred to the [^.]*Committee\b|\breferred to the Subcommittee\b",
    ),
    # Introduction last among the specific rules: "Introduced in House" is unambiguous,
    # but "Submitted in the Senate" is introduction only when nothing above matched.
    _rule(
        "introduction",
        "1000 10000 Intro-H Intro-S",
        r"^Introduced in (?:the )?(?:House|Senate)\b|^Submitted in (?:the )?(?:House|Senate)\b",
    ),
)


def classify(code: str | None, text: str) -> list[str]:
    """The classifications for one action, never empty."""
    text = text or ""
    found = [name for name, pattern in READINGS if pattern.search(text)]
    for rule in RULES:
        if rule.matches(code, text):
            for c in rule.classification:
                if c not in found:
                    found.append(c)
            break
    return found or ["other"]


_EXECUTIVE = {"became-law", "executive-signature", "executive-veto"}


def actor(action: Mapping[str, Any], classification: list[str]) -> str:
    """The `action_actors` code for one Congress.gov action."""
    if _EXECUTIVE.intersection(classification):
        return "executive"
    if action.get("type") == "Committee":
        return "committee"
    source = ((action.get("sourceSystem") or {}).get("name") or "").lower()
    if source.startswith("house"):
        return "lower"
    if source.startswith("senate"):
        return "upper"
    text = action.get("text") or ""
    house, senate = bool(re.search(r"\bHouse\b", text)), bool(re.search(r"\bSenate\b", text))
    if house and not senate:
        return "lower"
    if senate and not house:
        return "upper"
    return "legislature"


def normalize_action(action: Mapping[str, Any]) -> dict[str, Any]:
    text = (action.get("text") or "").strip()
    code = action.get("actionCode")
    classes = classify(code, text)
    committees = [c.get("name") for c in action.get("committees") or [] if c.get("name")]
    row: dict[str, Any] = {
        "date": action.get("actionDate"),
        "text": text,
        "actor": actor(action, classes),
        "classification": classes,
        "action_code": code,
        "action_type": action.get("type"),
        "source_system": (action.get("sourceSystem") or {}).get("name"),
    }
    if committees:
        row["committees"] = committees
    return row
