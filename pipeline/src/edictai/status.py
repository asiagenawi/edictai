"""Derive a bill's status from its dated actions.

Status is never stored in the YAML: storing it would let it drift from the
legislative history it summarizes. The values are the legislation_statuses
vocabulary in data/vocab/enums.yaml.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

INTRODUCED = "introduced"
IN_COMMITTEE = "in-committee"
PASSED_CHAMBER = "passed-chamber"
PASSED_LEGISLATURE = "passed-legislature"
ENACTED = "enacted"
VETOED = "vetoed"
FAILED = "failed"

STATUSES = (INTRODUCED, IN_COMMITTEE, PASSED_CHAMBER, PASSED_LEGISLATURE, ENACTED, VETOED, FAILED)

# Legislatures with one chamber, where a single passage completes the legislature's part.
UNICAMERAL = frozenset({"us-ne", "us-dc"})

_PROGRESS = {INTRODUCED: 0, IN_COMMITTEE: 1, PASSED_CHAMBER: 2, PASSED_LEGISLATURE: 3}

_FAILURES = {"failure", "committee-failure", "withdrawal", "died-at-session-end"}
_ENACTING = {"executive-signature", "became-law"}

# Measures whose legislative passage is the end of the line: they never go to the
# executive, so failing to reach one after passage is not a failure.
_COMPLETE_AT = {
    "resolution": PASSED_CHAMBER,
    "concurrent-resolution": PASSED_LEGISLATURE,
    "constitutional-amendment": PASSED_LEGISLATURE,
}


@dataclass(frozen=True)
class StatusResult:
    status: str
    # Date of the action that set the status; None when the status comes from the session ending.
    status_date: str | None
    introduced: str | None
    enacted: str | None
    last_action: str | None


def _classes(action: Mapping[str, Any]) -> set[str]:
    value = action.get("classification") or []
    return {value} if isinstance(value, str) else set(value)


def derive_status(
    actions: Iterable[Mapping[str, Any]],
    *,
    jurisdiction: str = "",
    classification: str = "bill",
    session_end: str | None = None,
    today: date | None = None,
) -> StatusResult:
    """Fold the actions, in date order, into a single status.

    Later actions win, so a bill that fails in committee and is then revived
    and passed reads as passed. Enactment is terminal. A bill still short of
    the legislature's final passage when its session has ended is failed; one
    that has passed both chambers stays passed-legislature, because governors
    routinely sign after adjournment.
    """
    ordered = sorted(actions, key=lambda a: str(a.get("date", "")))
    today = today or date.today()

    progress: str | None = None
    progress_date: str | None = None
    chambers: set[str] = set()
    failed_on: str | None = None
    vetoed_on: str | None = None
    override_chambers: set[str] = set()
    enacted_on: str | None = None
    introduced_on: str | None = None

    def advance(to: str, on: str) -> None:
        nonlocal progress, progress_date
        if progress is None or _PROGRESS[to] > _PROGRESS[progress]:
            progress, progress_date = to, on

    for i, action in enumerate(ordered):
        on = str(action.get("date"))
        classes = _classes(action)
        actor = action.get("actor")
        if introduced_on is None:
            introduced_on = on
        if enacted_on:
            continue

        if classes & _ENACTING:
            enacted_on = on
            continue

        if "executive-veto" in classes:
            vetoed_on = on
            override_chambers.clear()
            continue

        if "veto-override-passage" in classes and vetoed_on:
            # An override is complete once both chambers vote; an action attributed
            # to the whole legislature, or to no chamber, is taken as the final vote.
            if actor in ("lower", "upper") and jurisdiction not in UNICAMERAL:
                override_chambers.add(actor)
                if override_chambers >= {"lower", "upper"}:
                    enacted_on = on
            else:
                enacted_on = on
            continue

        failed_now = bool(classes & _FAILURES)
        moved = False

        if classes & {"introduction", "reading-1", "reading-2", "reading-3"}:
            advance(INTRODUCED, on)
            moved = True
        if classes & {"referral-committee", "committee-hearing", "committee-passage"}:
            advance(IN_COMMITTEE, on)
            moved = True
        if "passage" in classes:
            # Unattributed passages are counted as distinct chambers.
            chambers.add(actor if actor in ("lower", "upper") else f"unknown-{i}")
            if jurisdiction in UNICAMERAL or actor == "legislature" or len(chambers) >= 2:
                advance(PASSED_LEGISLATURE, on)
            else:
                advance(PASSED_CHAMBER, on)
            moved = True
        if "executive-receipt" in classes:
            advance(PASSED_LEGISLATURE, on)
            moved = True

        if failed_now:
            failed_on = on
        elif moved:
            failed_on = None
            vetoed_on = None if "passage" in classes else vetoed_on

    last_action = str(ordered[-1].get("date")) if ordered else None
    common = {"introduced": introduced_on, "enacted": enacted_on, "last_action": last_action}

    if enacted_on:
        return StatusResult(ENACTED, enacted_on, **common)
    if vetoed_on:
        return StatusResult(VETOED, vetoed_on, **common)
    if failed_on:
        return StatusResult(FAILED, failed_on, **common)

    current = progress or INTRODUCED
    complete_at = _COMPLETE_AT.get(classification)
    is_complete = complete_at is not None and _PROGRESS[current] >= _PROGRESS[complete_at]
    session_over = session_end is not None and date.fromisoformat(session_end) < today
    if session_over and not is_complete and _PROGRESS[current] < _PROGRESS[PASSED_LEGISLATURE]:
        return StatusResult(FAILED, None, **common)
    return StatusResult(current, progress_date or introduced_on, **common)
