"""Map LegiScan bill history to the `action_classifications` vocabulary.

Each history row is an action the legislature recorded. Its text is
classified with the patterns below: readings are added wherever the text
records one, the first matching rule in `RULES` supplies the main class, and
a referral is added to an introduction or reading ("First Reading; Referred
to Rules Committee"). Unmatched text is `other`.

LegiScan's `progress` events (introduced, engrossed, committee referral, and
so on) mark which recorded action reached each step. When an event's step is
missing from the text classification, it is added to the history row of the
same date, provided exactly one row that day can carry it. Progress events
never create actions: a date with no history row is ignored.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from ..status import UNICAMERAL


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


READINGS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("reading-1", _p(r"\b(?:first|1st) (?:reading|readers?)\b|\bread (?:for )?(?:the )?(?:first|1st) "
                     r"time\b|\bread once\b|\bintro(?:duced)?\.?,? (?:and )?read\b")),
    ("reading-2", _p(r"\b(?:second|2nd) reading\b|\bread (?:for )?(?:the )?(?:second|2nd) time\b|"
                     r"\bread twice\b")),
    ("reading-3", _p(r"\b(?:third|3rd|final) reading\b|\bread (?:for )?(?:a |the )?(?:third|3rd) time\b")),
)  # fmt: skip

# Placing a bill on a reading calendar is not the reading itself.
_SCHEDULED_READING = _p(
    r"\b(?:placed on|ordered to|order of|calendar(?: for)?|scheduled for|added to|"
    r"released to)\b[^.;]*?\b(?:first|second|third|1st|2nd|3rd) reading\b(?: calendar)?"
)

_EXEC = r"(?:governor|mayor|president(?! of (?:the )?senate| pro tem))"

# Order matters: the first matching rule supplies the main class.
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("veto-override-passage", _p(r"\bveto overrid(?:den|e (?:passed|adopted|successful))\b|"
                                 r"\boverrid(?:e|den)\b[^.;]*\bveto\b[^.;]*\b(?:passed|adopted)\b|"
                                 r"\bpassed[^.;]*\bover[^.;]*\bveto\b|"
                                 r"objections of the governor[^.;]*notwithstanding")),
    ("other", _p(r"\bsustain(?:ed|s)?\b[^.;]*\bveto\b|\bveto\b[^.;]*\bsustained\b|"
                 r"\bveto message\b|\bline[- ]item veto")),
    ("executive-veto", _p(rf"\b(?:pocket )?vetoed\b|\bveto(?:ed)? by (?:the )?{_EXEC}\b|"
                          rf"\b{_EXEC}'?s? veto\b(?![^.;]*(?:overrid|sustain|message))")),
    ("became-law", _p(r"\bbecame (?:public |private )?law\b|\bbecame effective\b|\bbecomes law\b|"
                      r"\bwithout (?:the )?(?:governor'?s? |mayor'?s? )?signature\b|"
                      r"^chaptered\b|\bchaptered by (?:the )?secretary of state\b|"
                      r"\bchapter (?:no\.? ?)?\d+[^.;]*\b(?:laws|statutes|acts)\b|"
                      r"\bpublic act (?:no\.? ?)?\d|\bact no\.? ?\d|\bsession law\b|"
                      r"\bassigned (?:act|chapter)\b|\blaw no\.? ?\d|^enacted\.?$|\bis now act\b|"
                      r"\bpub(?:lic|\.)? ch(?:apter|\.)? ?\d")),
    ("executive-signature", _p(rf"\b(?:signed|approved|enacted) by (?:the )?{_EXEC}\b|"
                               rf"\b{_EXEC} (?:signed|approved)\b|\bsigned by gov\b|"
                               rf"^signed(?: into law)?\.?$|\bapproved by gov\b")),
    ("executive-receipt", _p(rf"\b(?:sent|presented|delivered|transmitted|forwarded|submitted|"
                             rf"enrolled and sent) to (?:the )?{_EXEC}\b|\bto {_EXEC} for\b|"
                             rf"\b{_EXEC}'?s? desk\b|\breceived by (?:the )?{_EXEC}\b")),
    ("enrollment", _p(r"\benroll(?:ed|ment|ing)\b|\bsigned by (?:the )?(?:president|speaker|"
                      r"presiding officers?|lieutenant governor|officers)\b")),
    ("conference-committee", _p(r"\bconference committee\b|\bconferees?\b|"
                                r"\bconference report\b|\bconference (?:held|requested|appointed)\b")),
    ("died-at-session-end", _p(r"\bdied (?:in|on|at)\b|\bdied upon adjournment\b|"
                               r"\bfailed (?:upon|at) (?:sine die|adjournment)\b|"
                               r"\bdead at (?:session end|sine die)\b|"
                               r"\bindefinitely postponed at sine die\b")),
    ("committee-failure", _p(r"\b(?:failed|held|tabled|killed) in committee\b|"
                             r"\breported (?:unfavorably|adversely)\b|\bdo not pass\b|"
                             r"\bought not to pass\b|\binexpedient to legislate\b|"
                             r"\bfailed (?:to report|to pass) (?:out of )?committee\b|"
                             r"\bunfavorable report\b|\bcommittee report:? (?:unfavorable|"
                             r"rejected|failed)\b")),
    ("withdrawal", _p(r"\bwithdrawn by (?:the )?(?:sponsor|author|chief sponsor|patron)\b|"
                      r"^(?:bill |resolution )?withdrawn\b(?! from)|\bwithdrawn from "
                      r"(?:further )?consideration\b|\bsponsor withdr")),
    ("failure", _p(r"^(?:failed|defeated|lost)\b|\bfailed (?:of )?(?:passage|to pass|"
                   r"third reading|final passage)\b|\b(?:bill|resolution) (?:failed|defeated|"
                   r"lost)\b|\bpassage failed\b|\bthird reading[^.;]*\b(?:lost|failed)\b|"
                   r"\bmotion to pass failed\b|\bbills not passed\b|\bstrike enacting clause\b")),
    ("committee-passage", _p(r"\bdo pass\b|\breported (?:favorably|out|with|without|back|"
                             r"from committee|as substituted|as amended)\b|"
                             r"\b(?:favorable|favorably) (?:report|reported|recommend)|"
                             r"^favorable\b|\bought to pass\b|\brecommended (?:for passage|do pass|"
                             r"to pass|for adoption|that it pass)\b|\brecommends? that the "
                             r"(?:measure|bill|resolution) be (?:passed|adopted)\b|\breport(?:ed)? (?:adopted|"
                             r"favorable)\b|\bcommittee report(?:ed)?\b(?![^.;]*\b(?:unfavorabl|"
                             r"adverse))|\bpassed (?:out of )?committee\b|"
                             r"\bvoted out of committee\b|\brec(?:\.|ommended)? for pass|\b(?:\d+ ?)?dp\b|\bcomm rpt\b")),
    ("passage", _p(r"^passed\b|\breading,? passed\b|\bpassed;? (?:yeas|ayes)\b|"
                   r"\bpassed\b[^;]*\b\d+-\d+\b(?![^;]*committee)")),
    ("substitution", _p(r"\bsubstitute[ds]?\b[^.;]*\b(?:adopted|accepted|substituted)\b|"
                        r"\bsubstituted for\b|\bcommittee substitute\b|\bin lieu of\b")),
    ("passage", _p(r"\bconcur(?:s|red)? in (?:the )?(?:house |senate |assembly )?amendments?\b"
                   r"[^;]*\b(?:adopted|passed|\d+-\d+)|\bamendments? concurred in\b|"
                   r"\bconcur in and adopt\b|\bmotion to (?:read a third time and )?pass\b[^;]*"
                   r"\badopted\b|\bpass(?:ed)? as amended\b")),
    ("amendment-passage", _p(r"\bamendments?\b[^;]*?\b(?:adopted|agreed to|passed|accepted)\b|"
                             r"\b(?:adopted|agreed to) amendments?\b")),
    ("deferral", _p(r"\bpassed by (?:for the day|indefinitely)\b|\b(?:deferred|laid over|laid on (?:the )?table|tabled|postponed|held over|"
                    r"carried over|carryover|left pending|continued to|retained in committee|"
                    r"held in committee)\b")),
    ("passage", _p(r"\b(?:passed|adopted|agreed to|final passage)\b|^approved\b(?! by)")),
    ("committee-hearing", _p(r"\bhearings? (?:was )?(?:held|conducted|completed)\b|"
                             r"(?<!may be )\bheard in committee\b|^heard\b|\bwork session held\b|"
                             r"\bpublic hearing held\b|\bexecutive session (?:held|completed)\b|"
                             r"\bcommittee hearing held\b|\bset, (?:first|second|final) hearing\b")),
    ("referral-committee", _p(r"\breferred to\b|\bre-?referred\b|\breferrals?\b|"
                              r"\bassigned to\b[^.;]*\bcommittee\b|\bcommitted to\b|"
                              r"\bto (?:the )?committee on\b|^to (?:the )?\w[\w &,]*committee\b")),
    ("introduction", _p(r"\bintroduced\b|\bprefiled\b|\bpre-filed\b|^(?:bill )?filed\b|"
                        r"\bfiled with the (?:clerk|secretary|chief clerk)\b|\bintroduction\b|^intro\b|"
                        r"\bpresented to (?:the )?(?:house|senate|assembly|council)\b")),
)  # fmt: skip

_REFERRAL = next(p for n, p in RULES if n == "referral-committee")
_INTRODUCTION = next(p for n, p in RULES if n == "introduction")
# Text whose subject is an amendment, not the measure: "Amendment HA 1 to HS 1 -
# Introduced", "Senate Committee Amendment No. 1 Adopted".
_AMENDMENT_SUBJECT = _p(
    r"^\W*(?:(?:house|senate|assembly|floor|committee|council|joint|conference)\s+)*"
    r"(?:amendments?|amdts?\.?)\b"
)
# Agreeing to the other chamber's amendments completes passage.
_CONCURRENCE = _p(r"\bconcurred in\b|\bconcur(?:s|red)? in (?:the )?[\w ]*amendments?\b")
# Passage words that do not mean the measure passed.
_NOT_PASSAGE = _p(
    r"\bpassed by\b|\bsecond reading\b[^;]*\bpassed\b|\bpassed (?:first|second|1st|2nd) reading\b|"
    r"\bmotion\b(?![^;]*\bto (?:read a third time and )?(?:pass|concur)\b)|\breconsider|"
    r"\brules? (?:suspended|adopted)|\bcalendar\b|\bcompanion\b|\bpending\b"
)

_EXECUTIVE = {"executive-signature", "executive-veto", "became-law", "executive-receipt"}

# LegiScan progress event -> (class to add, classes that already satisfy it)
PROGRESS: dict[int, tuple[str, frozenset[str]]] = {
    1: ("introduction", frozenset({"introduction"})),
    2: ("passage", frozenset({"passage"})),
    3: ("enrollment", frozenset({"enrollment", "passage", "executive-receipt"})),
    5: ("executive-veto", frozenset({"executive-veto"})),
    6: (
        "failure",
        frozenset({"failure", "committee-failure", "withdrawal", "died-at-session-end"}),
    ),
    7: ("veto-override-passage", frozenset({"veto-override-passage"})),
    9: ("referral-committee", frozenset({"referral-committee"})),
    10: ("committee-passage", frozenset({"committee-passage"})),
    11: ("committee-failure", frozenset({"committee-failure"})),
}
# Events 4 (passed) and 8 (chaptered) mark enactment, which LegiScan may
# record without saying whether the governor signed; they are never added,
# because the vocabulary distinguishes signature from becoming law.


def classify_text(text: str, *, federal: bool = False) -> list[str]:
    """The classifications for one action text, never empty.

    In a state, an unqualified "President" is the Senate's presiding officer,
    so "Signed by President" is enrollment, not the executive's signature.
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    if not federal:
        text = re.sub(r"\bpresident\b", "presiding officer", text, flags=re.IGNORECASE)
    reading_text = _SCHEDULED_READING.sub(" ", text)
    found = [name for name, pattern in READINGS if pattern.search(reading_text)]
    if _AMENDMENT_SUBJECT.search(text):
        if _CONCURRENCE.search(text):
            return [*found, "passage"]
        amended = next(p for n, p in RULES if n == "amendment-passage")
        if amended.search(text):
            found.append("amendment-passage")
        return found or ["other"]
    main = None
    for name, pattern in RULES:
        if name == "passage" and _NOT_PASSAGE.search(text):
            continue
        if pattern.search(text):
            main = name
            break
    if main and main not in found:
        found.append(main)
    if main in (None, "introduction") and _REFERRAL.search(text):
        found.append("referral-committee")
    if main == "referral-committee" and _INTRODUCTION.search(text):
        found.insert(found.index(main), "introduction")
    return found or ["other"]


def actor_for(chamber: str | None, classes: Iterable[str], jurisdiction: str) -> str:
    classes = set(classes)
    if classes & _EXECUTIVE:
        return "executive"
    if jurisdiction in UNICAMERAL:
        return "legislature"
    code = (chamber or "").upper()
    if code in ("H", "A"):
        return "lower"
    if code == "S":
        return "upper"
    return "legislature"


def map_history(
    history: Iterable[Mapping[str, Any]],
    progress: Iterable[Mapping[str, Any]] = (),
    *,
    jurisdiction: str,
) -> list[dict[str, Any]]:
    """Record-format actions from LegiScan history, in date order."""
    rows = [
        h for h in history if isinstance(h.get("date"), str) and (h.get("action") or "").strip()
    ]
    rows.sort(key=lambda h: h["date"])  # stable: keeps LegiScan's order within a day
    actions: list[dict[str, Any]] = []
    for h in rows:
        classes = classify_text(str(h["action"]), federal=jurisdiction == "us")
        actions.append(
            {
                "date": h["date"],
                "description": re.sub(r"\s+", " ", str(h["action"])).strip(),
                "_chamber": h.get("chamber"),
                "_importance": h.get("importance"),
                "classification": classes,
            }
        )

    by_date: dict[str, list[dict[str, Any]]] = {}
    for a in actions:
        by_date.setdefault(a["date"], []).append(a)
    for event in progress:
        hint = PROGRESS.get(int(event.get("event") or 0))
        day = by_date.get(str(event.get("date")))
        if not hint or not day:
            continue
        add, satisfied = hint
        if any(satisfied & set(a["classification"]) for a in day):
            continue
        important = [a for a in day if a.get("_importance") == 1]
        if len(important) == 1:
            target = important[0]
        elif len(day) == 1 or add == "introduction":
            target = day[0]  # LegiScan lists a day's actions in order; filing comes first
        else:
            continue
        target["classification"] = [c for c in target["classification"] if c != "other"] + [add]

    out = []
    for a in actions:
        out.append(
            {
                "date": a["date"],
                "description": a["description"],
                "actor": actor_for(a["_chamber"], a["classification"], jurisdiction),
                "classification": a["classification"],
            }
        )
    return out
