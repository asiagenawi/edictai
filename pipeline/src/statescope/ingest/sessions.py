"""Fill missing session dates in data/jurisdictions/ from the Open States API.

Only sessions that legislation records use are considered, and only a
missing `start_date` or `end_date` is written; existing values are never
changed. Each filled session gets a `sources` entry citing the Open States
API URL that supplied the date, with kind `secondary`.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..paths import Paths
from ..records import load_records
from ..yamlio import load_yaml
from .http import HTTPError, RateLimiter, Transport, urllib_transport
from .session_ids import FIRST_CONGRESS_YEAR

API_URL = "https://v3.openstates.org"
# Jurisdictions whose Open States end dates mark a spring adjournment rather
# than the end of the legislature, so they are never copied.
UNRELIABLE_END_DATES = {
    "us-il": "Open States end dates for Illinois are spring adjournments; "
    "the General Assembly continues after them",
}
# The free tier allows 10 requests a minute.
MIN_INTERVAL = 6.5


class OpenStatesError(Exception):
    pass


def ocd_jurisdiction(jurisdiction: str) -> str:
    if jurisdiction == "us":
        return "ocd-jurisdiction/country:us/government"
    return f"ocd-jurisdiction/country:us/state:{jurisdiction[3:]}/government"


def jurisdiction_url(jurisdiction: str) -> str:
    """The public URL of the API resource, without the key."""
    path = urllib.parse.quote(ocd_jurisdiction(jurisdiction), safe="/:")
    return f"{API_URL}/jurisdictions/{path}?include=legislative_sessions"


class OpenStatesClient:
    def __init__(
        self,
        api_key: str,
        cache_dir: Path,
        *,
        transport: Transport | None = None,
        limiter: RateLimiter | None = None,
    ):
        if not api_key:
            raise OpenStatesError("OPENSTATES_API_KEY is not set")
        self._key = api_key
        self.cache_dir = cache_dir
        self.transport = transport or urllib_transport()
        self.limiter = limiter or RateLimiter(MIN_INTERVAL)
        self.requests = 0

    def __repr__(self) -> str:
        return f"OpenStatesClient(cache_dir={str(self.cache_dir)!r})"

    def _cache_path(self, jurisdiction: str) -> Path:
        return self.cache_dir / "jurisdictions" / f"{jurisdiction}.json"

    def jurisdiction(self, jurisdiction: str, *, refresh: bool = False) -> dict[str, Any] | None:
        """The jurisdiction with its legislative sessions; None if Open States lacks it."""
        path = self._cache_path(jurisdiction)
        if path.exists() and not refresh:
            cached = json.loads(path.read_text(encoding="utf-8"))
            return cached.get("response")
        url = jurisdiction_url(jurisdiction)
        base, _, query = url.partition("?")
        self.limiter.wait()
        self.requests += 1
        try:
            body = self.transport(
                base, dict(urllib.parse.parse_qsl(query)), {"X-API-KEY": self._key}
            )
        except HTTPError as exc:
            if "HTTP 404" in str(exc):
                response = None
            else:
                raise OpenStatesError(str(exc)) from None
        else:
            try:
                response = json.loads(body)
            except ValueError:
                raise OpenStatesError(f"{url}: response is not JSON") from None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"url": url, "accessed": date.today().isoformat(), "response": response},
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
        return response

    def accessed(self, jurisdiction: str) -> str:
        path = self._cache_path(jurisdiction)
        return json.loads(path.read_text(encoding="utf-8"))["accessed"]


class CachedOpenStates:
    """Read-only view of the Open States jurisdiction cache, for runs without an API key."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    def _cache_path(self, jurisdiction: str) -> Path:
        return self.cache_dir / "jurisdictions" / f"{jurisdiction}.json"

    def jurisdiction(self, jurisdiction: str) -> dict[str, Any] | None:
        path = self._cache_path(jurisdiction)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8")).get("response")

    def accessed(self, jurisdiction: str) -> str:
        path = self._cache_path(jurisdiction)
        return json.loads(path.read_text(encoding="utf-8"))["accessed"]


def source_for(jurisdiction: str, response: Mapping[str, Any], accessed: str) -> dict[str, str]:
    """The `sources` item citing the Open States API for a session's dates."""
    return {
        "url": jurisdiction_url(jurisdiction),
        "title": f"Open States API, {response.get('name', jurisdiction)} legislative sessions",
        "publisher": "Open States",
        "kind": "secondary",
        "accessed": accessed,
    }


# -- matching -----------------------------------------------------------------


def _years(identifier: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{4})(?:-(\d{4}))?", identifier)
    if not m:
        return None
    start = int(m.group(1))
    return start, int(m.group(2) or start)


def _is_primary(session: Mapping[str, Any]) -> bool:
    """Open States leaves classification empty for some states' regular sessions."""
    kind = session.get("classification") or ""
    if kind:
        return kind == "primary"
    return not re.search(r"special|extraordinary", str(session.get("name", "")), re.IGNORECASE)


def match_session(
    ours: Mapping[str, Any], theirs: list[Mapping[str, Any]], jurisdiction: str
) -> Mapping[str, Any] | None:
    """The Open States session that corresponds to one of ours, if unambiguous.

    Matches on identical identifiers first; otherwise on a primary session
    with the same start date, or (for regular sessions) the same span of years.
    """
    ident = str(ours["identifier"])
    primary = [s for s in theirs if _is_primary(s)]
    exact = [s for s in theirs if str(s.get("identifier")) == ident]
    if len(exact) == 1:
        return exact[0]
    start = ours.get("start_date")
    if start:
        same_start = [s for s in primary if s.get("start_date") == start]
        if len(same_start) == 1:
            return same_start[0]
    if ours.get("classification", "primary") != "primary" or jurisdiction == "us":
        return None
    years = _years(ident)
    if not years:
        return None
    hits = []
    for s in primary:
        sy = _years(str(s.get("identifier", "")))
        if sy == years:
            hits.append(s)
            continue
        s_start, s_end = s.get("start_date") or "", s.get("end_date") or ""
        if (
            len(s_start) >= 4
            and len(s_end) >= 4
            and (int(s_start[:4]), int(s_end[:4])) == years
            and sy is None
        ):
            hits.append(s)
    return hits[0] if len(hits) == 1 else None


# -- YAML editing -------------------------------------------------------------


def _entry_spans(lines: list[str]) -> dict[str, tuple[int, int]]:
    """identifier -> (first line, end line) of each `sessions:` list item."""
    spans: dict[str, tuple[int, int]] = {}
    try:
        top = next(i for i, line in enumerate(lines) if line.rstrip() == "sessions:")
    except StopIteration:
        return spans
    starts: list[tuple[str, int]] = []
    end = len(lines)
    for i in range(top + 1, len(lines)):
        line = lines[i]
        if line and not line.startswith(" "):
            end = i
            break
        m = re.match(r"  - identifier:\s*(.+?)\s*$", line)
        if m:
            starts.append((m.group(1).strip("'\""), i))
    for n, (ident, first) in enumerate(starts):
        last = starts[n + 1][1] if n + 1 < len(starts) else end
        while last > first + 1 and not lines[last - 1].strip():
            last -= 1
        spans[ident] = (first, last)
    return spans


def _quote_style(block: list[str]) -> str:
    for line in block:
        m = re.match(r"\s+start_date:\s*(['\"]?)\d", line)
        if m:
            return m.group(1)
    return '"'


# Keys of a session entry in schema order, for placing inserted lines.
_SESSION_KEYS = ("identifier", "name", "classification", "start_date", "end_date")


def edit_session_yaml(
    text: str, identifier: str, updates: Mapping[str, str], source: Mapping[str, str]
) -> str:
    """Insert date fields and a source into one session entry, leaving other lines verbatim."""
    lines = text.splitlines()
    spans = _entry_spans(lines)
    if identifier not in spans:
        raise ValueError(f"session {identifier!r} not found")
    first, last = spans[identifier]
    block = lines[first:last]
    q = _quote_style(block)
    for key in ("start_date", "end_date"):
        if key not in updates:
            continue
        earlier = _SESSION_KEYS[: _SESSION_KEYS.index(key)]
        anchor = max(
            i
            for i, line in enumerate(block)
            if i == 0 or re.match(rf"    ({'|'.join(earlier)}):", line)
        )
        block.insert(anchor + 1, f"    {key}: {q}{updates[key]}{q}")
    item = [
        f'      - url: "{source["url"]}"',
        f'        title: "{source["title"]}"',
        f"        publisher: {source['publisher']}",
        f"        kind: {source['kind']}",
        f"        accessed: {q}{source['accessed']}{q}",
    ]
    if any(line.strip() == "sources:" for line in block):
        at = max(i for i, line in enumerate(block) if line.startswith("      ")) + 1
        block[at:at] = item
    else:
        block.extend(["    sources:", *item])
    out = lines[:first] + block + lines[last:]
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


# -- driver -------------------------------------------------------------------


@dataclass
class SessionChange:
    jurisdiction: str
    identifier: str
    fields: dict[str, str]
    openstates_identifier: str
    url: str


@dataclass
class SessionsResult:
    changes: list[SessionChange] = field(default_factory=list)
    skipped: list[tuple[str, str, str]] = field(default_factory=list)  # (jur, ident, why)
    complete: list[tuple[str, str]] = field(default_factory=list)
    requests: int = 0


# Steps that follow adjournment in the ordinary course: the governor acts on
# bills after the legislature has gone home.
_POST_SESSION = {"executive-receipt", "executive-signature", "executive-veto", "became-law"}


def _legislative(action: Mapping[str, Any]) -> bool:
    if action.get("actor") == "executive":
        return False
    classes = action.get("classification") or []
    classes = {classes} if isinstance(classes, str) else set(classes)
    return not (classes and classes <= _POST_SESSION)


def session_usage(paths: Paths) -> dict[tuple[str, str], str]:
    """(jurisdiction, session) -> latest legislative action date among its records."""
    used: dict[tuple[str, str], str] = {}
    for record in load_records(paths.data, ["legislation"]):
        jur, session = record.data.get("jurisdiction"), record.data.get("session")
        if not isinstance(jur, str) or session is None:
            continue
        dates = [
            a["date"]
            for a in record.data.get("actions") or []
            if isinstance(a, dict) and isinstance(a.get("date"), str) and _legislative(a)
        ]
        key = (jur, str(session))
        used[key] = max([used.get(key, ""), *dates])
    return used


def _iso(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value[:10]):
        return value[:10]
    return None


def plausibility(
    jurisdiction: str,
    ours: Mapping[str, Any],
    fields: Mapping[str, str],
    last_action: str,
    *,
    today: str,
) -> str | None:
    """Why the Open States dates cannot be the session's own, or None if they can.

    Open States often records the end of a two-year legislature's first
    annual meeting as the session end, so the end year must match the
    session's last year, and no recorded legislative action may fall after
    it. A future end date is usually a calendar placeholder and is never
    accepted.
    """
    start = fields.get("start_date") or ours.get("start_date")
    end = fields.get("end_date") or ours.get("end_date")
    if start and end and str(end) < str(start):
        return f"end {end} precedes start {start}"
    ident = str(ours["identifier"])
    if "end_date" in fields:
        if jurisdiction in UNRELIABLE_END_DATES:
            return UNRELIABLE_END_DATES[jurisdiction]
        if fields["end_date"] > today:
            return f"end {fields['end_date']} is in the future (session in progress)"
        end_year = int(fields["end_date"][:4])
        years = _years(ident)
        if jurisdiction == "us" and ident.isdigit():
            expected = FIRST_CONGRESS_YEAR + 2 * int(ident)
            if end_year != expected:
                return f"end {fields['end_date']} is not in {expected}"
        elif years and ours.get("classification", "primary") == "primary":
            if end_year != years[1]:
                return f"end {fields['end_date']} is not in the session's last year {years[1]}"
        if last_action and last_action > fields["end_date"]:
            return f"end {fields['end_date']} precedes a recorded action on {last_action}"
    return None


def update_sessions(
    paths: Paths,
    client: OpenStatesClient,
    *,
    dry_run: bool = False,
    today: date | None = None,
    progress: Callable[[str], None] | None = None,
) -> SessionsResult:
    today_iso = (today or date.today()).isoformat()
    result = SessionsResult()
    usage = session_usage(paths)
    by_jur: dict[str, list[str]] = {}
    for jur, ident in sorted(usage):
        by_jur.setdefault(jur, []).append(ident)
    for jur, idents in by_jur.items():
        path = paths.data / "jurisdictions" / f"{jur}.yaml"
        if not path.exists():
            continue
        data = load_yaml(path)
        ours_by_id = {str(s.get("identifier")): s for s in data.get("sessions") or []}
        needed = []
        for ident in idents:
            ours = ours_by_id.get(ident)
            if ours is None:
                result.skipped.append((jur, ident, "not listed in the jurisdiction file"))
                continue
            missing = [k for k in ("start_date", "end_date") if not ours.get(k)]
            if missing:
                needed.append((ident, ours, missing))
            else:
                result.complete.append((jur, ident))
        if not needed:
            continue
        if progress:
            progress(f"Open States {jur}")
        response = client.jurisdiction(jur)
        theirs = (response or {}).get("legislative_sessions") or []
        original = text = path.read_text(encoding="utf-8")
        for ident, ours, missing in needed:
            match = match_session(ours, theirs, jur) if theirs else None
            if match is None:
                why = "no Open States jurisdiction" if response is None else "no matching session"
                result.skipped.append((jur, ident, why))
                continue
            label = f"Open States {match.get('identifier')!r}"
            fields = {k: v for k in missing if (v := _iso(match.get(k)))}
            if not fields:
                result.skipped.append((jur, ident, f"{label} has no {', '.join(missing)}"))
                continue
            why = plausibility(
                jur,
                ours,
                fields,
                usage[(jur, ident)],
                today=today_iso,
            )
            if why:
                result.skipped.append((jur, ident, f"{label}: {why}"))
                continue
            source = source_for(jur, response, client.accessed(jur))
            text = edit_session_yaml(text, ident, fields, source)
            result.changes.append(
                SessionChange(jur, ident, fields, str(match.get("identifier")), source["url"])
            )
        if not dry_run and text != original:
            path.write_text(text, encoding="utf-8")
    result.requests = client.requests
    return result
