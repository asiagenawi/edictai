"""Turn checked full-stage judgments into data/legislation records.

Only in-scope judgments with no errors become records. Everything except
the reviewer's scope call, codes, summary, and provisions comes from the
cached LegiScan bill: actions (mapped from the recorded history, never
inferred), sponsors, versions (the legislature's own `state_link` URLs, never
legiscan.com), and sources. A record is `machine-verified` only when its text
was retrieved and every provision quote was found verbatim in it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from ..ingest.bill_numbers import match_key
from ..ingest.candidates import existing_records
from ..ingest.sessions import _iso, match_session, plausibility, source_for
from ..paths import Paths
from ..status import UNICAMERAL
from ..yamlio import load_yaml
from .actions import map_history
from .common import BillStore, ClassifyPaths, latest_text, real_date, slug_for
from .judgments import CheckReport
from .texts import TextStore
from .yamlwrite import dump

METHOD = "legiscan-text+classifier"
ATTRIBUTION_NOTE = (
    "Actions, sponsors, and versions from the LegiScan API (https://legiscan.com), "
    "CC BY 4.0; scope, codes, summary, and provisions by the classifier from the bill text."
)

# LegiScan bill_type -> legislation_classifications
BILL_TYPES = {
    "B": "bill",
    "R": "resolution",
    "CR": "concurrent-resolution",
    "JR": "joint-resolution",
    "JRCA": "constitutional-amendment",
    "CA": "constitutional-amendment",
    "M": "resolution",
    "JM": "joint-resolution",
    "CM": "concurrent-resolution",
    "SR": "resolution",
}


def _official(url: str | None) -> str | None:
    if not url or not re.match(r"^https?://", url) or "legiscan.com" in url.lower():
        return None
    return url


def map_sponsors(sponsors: Iterable[Mapping[str, Any]], jurisdiction: str) -> list[dict[str, Any]]:
    out = []
    for s in sorted(sponsors, key=lambda s: s.get("sponsor_order") or 999):
        name = (s.get("name") or "").strip()
        if not name:
            continue
        entry: dict[str, Any] = {"name": name}
        if s.get("committee_sponsor"):
            entry["classification"] = "committee"
        entry["primary"] = s.get("sponsor_type_id") == 1
        if s.get("party") and not s.get("committee_sponsor"):
            entry["party"] = str(s["party"])
        role = (s.get("role") or "").lower()
        if jurisdiction not in UNICAMERAL and not s.get("committee_sponsor"):
            if role in ("rep", "asm", "del", "assemblymember"):
                entry["chamber"] = "lower"
            elif role == "sen":
                entry["chamber"] = "upper"
        out.append(entry)
    return out


def map_versions(texts: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    rows = sorted(texts, key=lambda t: (t.get("date") or "", t.get("doc_id") or 0))
    for t in rows:
        url = _official(t.get("state_link"))
        if not url:
            continue
        v: dict[str, Any] = {"note": t.get("type") or "Text"}
        if real_date(t.get("date")):
            v["date"] = t["date"]
        v["url"] = url
        if t.get("mime"):
            v["media_type"] = t["mime"]
        out.append(v)
    return out


@dataclass
class Built:
    key: str
    record: dict[str, Any] | None
    path: Path | None
    problems: list[str] = field(default_factory=list)
    dropped_provisions: list[int] = field(default_factory=list)


def build_record(
    judgment: Mapping[str, Any],
    candidate: Mapping[str, Any],
    bill: Mapping[str, Any] | None,
    store: TextStore,
    *,
    today: date,
    quote_failures: Iterable[int] = (),
) -> Built:
    key = judgment["key"]
    source = bill or candidate
    jurisdiction = candidate["jurisdiction"]
    session = str(candidate["session"])
    identifier = candidate["identifier"]
    slug = slug_for(identifier)
    built = Built(key, None, Path("legislation") / jurisdiction / session / f"{slug}.yaml")

    meta, text = store.for_key(key)
    text_url = _official((meta or {}).get("state_link"))
    if text_url is None and meta is None:
        latest = latest_text(source.get("texts") or [])
        text_url = _official((latest or {}).get("state_link"))
    bill_url = _official(source.get("state_link") or candidate.get("url"))
    if not bill_url and not text_url:
        built.problems.append("no official URL for the bill or its text; not imported")
        return built

    actions = map_history(
        source.get("history") or [], source.get("progress") or [], jurisdiction=jurisdiction
    )
    actions = [a for a in actions if a["date"] <= today.isoformat()]
    if not actions:
        built.problems.append("no recorded actions; not imported")
        return built

    failures = set(quote_failures)
    provisions = []
    cite_url = text_url or bill_url
    for i, p in enumerate(judgment.get("provisions") or []):
        if i in failures or text is None:
            built.dropped_provisions.append(i)
            continue
        prov: dict[str, Any] = {"type": p["type"], "description": p["description"].strip()}
        if p.get("education_levels"):
            prov["education_levels"] = list(p["education_levels"])
        prov["citation"] = {
            "source_url": cite_url,
            "locator": p["locator"].strip(),
            "quote": re.sub(r"\s+", " ", p["quote"]).strip(),
        }
        provisions.append(prov)

    accessed = today.isoformat()
    sources = []
    if bill_url:
        sources.append(
            {
                "url": bill_url,
                "title": f"{identifier} bill page",
                "kind": "official",
                "accessed": accessed,
            }
        )
    if text_url and text_url != bill_url:
        title = f"{identifier} text"
        if meta and meta.get("type"):
            title += f" ({meta['type']})"
        sources.append({"url": text_url, "title": title, "kind": "official", "accessed": accessed})

    notes = [ATTRIBUTION_NOTE]
    verified = (
        text is not None and not failures and bool(provisions) and not built.dropped_provisions
    )
    if text is None:
        reason = (meta or {}).get("reason") or "text not fetched"
        notes.append(f"Bill text unavailable ({reason}); classified from metadata.")
    if built.dropped_provisions:
        notes.append(
            f"{len(built.dropped_provisions)} provision(s) dropped: quote not verified in the text."
        )
    if text is not None and not judgment.get("provisions"):
        notes.append("No quoted provisions.")
    verification: dict[str, Any] = {"status": "machine-verified" if verified else "unverified"}
    if verified:
        verification["last_verified"] = accessed
        verification["method"] = METHOD
    verification["notes"] = " ".join(notes)

    record: dict[str, Any] = {
        "id": f"{jurisdiction}-{session}-{slug}",
        "jurisdiction": jurisdiction,
        "session": session,
        "identifier": identifier,
        "title": (source.get("title") or candidate.get("title") or identifier).strip(),
        "classification": BILL_TYPES.get(str(source.get("bill_type") or "B"), "bill"),
        "education_levels": list(judgment["education_levels"]),
        "topics": list(judgment["topics"]),
        "summary": re.sub(r"\s+", " ", judgment["summary"]).strip(),
    }
    if provisions:
        record["provisions"] = provisions
    sponsors = map_sponsors(source.get("sponsors") or [], jurisdiction)
    if sponsors:
        record["sponsors"] = sponsors
    record["actions"] = actions
    versions = map_versions(source.get("texts") or [])
    if versions:
        record["versions"] = versions
    if candidate.get("bill_id"):
        record["external_ids"] = {"legiscan_bill_id": int(candidate["bill_id"])}
    record["sources"] = sources
    record["verification"] = verification
    built.record = record
    return built


# -- sessions -----------------------------------------------------------------


class SessionDates(Protocol):
    """Where session dates come from: an OpenStatesClient or its read-only cache."""

    def jurisdiction(self, jurisdiction: str) -> dict[str, Any] | None: ...

    def accessed(self, jurisdiction: str) -> str: ...


def session_entry(
    session: Mapping[str, Any],
    bills: BillStore,
    identifier: str,
    *,
    jurisdiction: str | None = None,
    openstates: SessionDates | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """identifier, name, classification, dates, legiscan_session_id for a LegiScan session.

    LegiScan does not publish session dates. The matching Open States session
    supplies them when there is one, cited as a source; its end date is kept
    only when it is plausible and not in the future. Otherwise start_date is
    the earliest recorded action dated in or after the session's first year
    among the session's bills, so prefiling before the year begins is not
    counted, and no end date is written.
    """
    year = int(session.get("year_start") or identifier[:4])
    floor = f"{year}-01-01"
    dates = []
    for bill in bills.session_bills(int(session["session_id"])):
        for h in bill.get("history") or []:
            d = h.get("date")
            if isinstance(d, str) and len(d) == 10:
                dates.append(d)
    entry: dict[str, Any] = {
        "identifier": identifier,
        "name": session.get("session_name") or session.get("session_title") or identifier,
        "classification": "special" if session.get("special") else "primary",
    }
    found = _openstates_dates(entry, jurisdiction, openstates, max(dates, default=""), today)
    sources = found.pop("sources") if found else None
    if found:
        entry.update(found)
    elif later := [d for d in dates if d >= floor]:
        entry["start_date"] = min(later)
    entry["legiscan_session_id"] = int(session["session_id"])
    if sources:
        entry["sources"] = sources
    return entry


def _openstates_dates(
    ours: Mapping[str, Any],
    jurisdiction: str | None,
    openstates: SessionDates | None,
    last_action: str,
    today: date | None,
) -> dict[str, Any] | None:
    if openstates is None or jurisdiction is None:
        return None
    response = openstates.jurisdiction(jurisdiction)
    theirs = (response or {}).get("legislative_sessions") or []
    match = match_session(ours, theirs, jurisdiction) if theirs else None
    start = _iso((match or {}).get("start_date"))
    if not start:
        return None
    out: dict[str, Any] = {"start_date": start}
    end = _iso(match.get("end_date"))
    if end and not plausibility(
        jurisdiction,
        {**ours, "start_date": start},
        {"end_date": end},
        last_action,
        today=(today or date.today()).isoformat(),
    ):
        out["end_date"] = end
    out["sources"] = [source_for(jurisdiction, response, openstates.accessed(jurisdiction))]
    return out


def append_session(text: str, entry: Mapping[str, Any]) -> str:
    """Add a session item to the jurisdiction file's `sessions:` list, leaving other lines intact."""
    lines = [
        f'  - identifier: "{entry["identifier"]}"',
        f"    name: {_plain(entry['name'])}",
        f"    classification: {entry['classification']}",
    ]
    for key in ("start_date", "end_date"):
        if entry.get(key):
            lines.append(f'    {key}: "{entry[key]}"')
    lines.append(f"    legiscan_session_id: {entry['legiscan_session_id']}")
    if entry.get("sources"):
        lines.append("    sources:")
        for src in entry["sources"]:
            lines += [
                f'      - url: "{src["url"]}"',
                f"        title: {_plain(src['title'])}",
                f"        publisher: {_plain(src['publisher'])}",
                f"        kind: {src['kind']}",
                f'        accessed: "{src["accessed"]}"',
            ]
    rows = text.rstrip("\n").split("\n")
    try:
        top = next(i for i, ln in enumerate(rows) if ln.rstrip() == "sessions:")
    except StopIteration:
        return "\n".join([*rows, "sessions:", *lines]) + "\n"
    end = len(rows)
    for i in range(top + 1, len(rows)):
        if rows[i] and not rows[i].startswith(" "):
            end = i
            break
    while end > top + 1 and not rows[end - 1].strip():
        end -= 1
    return "\n".join(rows[:end] + lines + rows[end:]) + "\n"


def _plain(value: str) -> str:
    return dump({"v": value}).split(": ", 1)[1].strip()


# -- driver -------------------------------------------------------------------


@dataclass
class ImportResult:
    written: list[Path] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (key, why)
    out_of_scope: int = 0
    rejected: list[tuple[str, list[str]]] = field(default_factory=list)
    machine_verified: int = 0
    unverified: int = 0
    sessions_added: list[tuple[str, str]] = field(default_factory=list)
    dropped_provisions: int = 0


def import_judgments(
    cp: ClassifyPaths,
    target: Paths,
    report: CheckReport,
    candidates: Mapping[str, Mapping[str, Any]],
    sessions: Mapping[str, Mapping[str, Any]],
    *,
    today: date,
    overwrite: bool = False,
    dry_run: bool = False,
    strict_quotes: bool = False,
    openstates: SessionDates | None = None,
) -> ImportResult:
    """Write records for every accepted in-scope judgment into target/data."""
    result = ImportResult()
    store = TextStore(cp.texts)
    bills = BillStore(cp.legiscan)
    existing = existing_records(target.data)
    listed: dict[str, set[str]] = {}
    pending_sessions: dict[str, list[dict[str, Any]]] = {}
    try:
        for checked in report.items:
            row = checked.row
            # Quote failures do not reject a judgment: those provisions are dropped.
            fatal = [
                e
                for e in checked.errors
                if strict_quotes or not re.match(r"provisions/\d+: (?:quote|no stored text)", e)
            ]
            if fatal:
                result.rejected.append((checked.key, fatal))
                continue
            if not row.get("in_scope"):
                result.out_of_scope += 1
                continue
            cand = candidates.get(checked.key)
            if cand is None:
                result.skipped.append((checked.key, "not in the candidate file"))
                continue
            jur, session = cand["jurisdiction"], str(cand["session"])
            mk = match_key(jur, cand["identifier"])
            prior = existing.get((jur, session, mk)) if mk else None
            if cand.get("existing_record") or (prior and not overwrite):
                result.skipped.append(
                    (checked.key, f"already recorded as {cand.get('existing_record') or prior}")
                )
                continue
            bill = bills.get(int(cand["bill_id"]))
            built = build_record(
                row, cand, bill, store, today=today, quote_failures=checked.quote_failures
            )
            if built.record is None:
                result.skipped.append((checked.key, "; ".join(built.problems)))
                continue
            path = target.data / built.path
            if path.exists() and not overwrite:
                result.skipped.append((checked.key, f"{built.path} exists"))
                continue
            result.dropped_provisions += len(built.dropped_provisions)
            if built.record["verification"]["status"] == "machine-verified":
                result.machine_verified += 1
            else:
                result.unverified += 1
            # Session listed in the jurisdiction file?
            if jur not in listed:
                jpath = target.data / "jurisdictions" / f"{jur}.yaml"
                data = load_yaml(jpath) if jpath.exists() else {}
                listed[jur] = {str(s.get("identifier")) for s in (data or {}).get("sessions") or []}
            if session not in listed[jur]:
                info = sessions.get(str(cand.get("legiscan_session_id")))
                if info is not None:
                    entry = session_entry(
                        info, bills, session, jurisdiction=jur, openstates=openstates, today=today
                    )
                    if "start_date" in entry:
                        pending_sessions.setdefault(jur, []).append(entry)
                        listed[jur].add(session)
                        result.sessions_added.append((jur, session))
            if not dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(dump(built.record), encoding="utf-8")
            result.written.append(path)
    finally:
        bills.close()
    if not dry_run:
        for jur, entries in pending_sessions.items():
            jpath = target.data / "jurisdictions" / f"{jur}.yaml"
            if not jpath.exists():
                continue
            text = jpath.read_text(encoding="utf-8")
            for entry in sorted(entries, key=lambda e: e.get("start_date", "")):
                text = append_session(text, entry)
            jpath.write_text(text, encoding="utf-8")
    return result
