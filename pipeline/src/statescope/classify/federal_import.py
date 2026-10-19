"""Turn checked full-stage judgments of federal candidates into records.

- Bills become `data/legislation/us/{congress}/{slug}.yaml`. Actions,
  sponsors, versions, and identifiers come from the Congress.gov API row
  in the candidate file (actions as Congress.gov recorded them, never
  inferred); sources and citations are congress.gov URLs.
- Executive orders become `data/executive-actions/us/eo-{number}.yaml`,
  issued by `us-president`, cited to the order's federalregister.gov page.
- Other Federal Register documents become `data/documents/us/fr-{number}.yaml`,
  issued by the agency's organization record.

As for state bills, only the reviewer's scope call, codes, summary, and
provisions come from the judgment, and every provision quote must be found
verbatim in the fetched text or the provision is dropped. A record is
`machine-verified` only when its text was retrieved and every quote was found.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..federal.congress import ordinal
from ..paths import Paths
from .common import ClassifyPaths, read_json
from .federal import EXECUTIVE_ACTION, actions, order_versions, record_kind
from .importer import ImportResult
from .judgments import CheckReport
from .texts import TextStore
from .textutil import quote_in_text
from .yamlwrite import dump

BILL_METHOD = "congress-gov-text+classifier"
FR_METHOD = "federal-register-text+classifier"
BILL_NOTE = (
    "Actions, sponsors, and versions from the Congress.gov API; scope, codes, summary, "
    "and provisions by the classifier from the bill text."
)
FR_NOTE = (
    "Dates and identifiers from the Federal Register API; scope, codes, summary, and "
    "provisions by the classifier from the document text."
)
# Federal Register agency slug -> organization id.
AGENCY_ORGS = {"education-department": "us-ed"}
_NOTE_DATE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|"
    r"December) (\d{1,2}), (\d{4})"
)


@dataclass
class Built:
    key: str
    record: dict[str, Any] | None
    path: Path | None
    problems: list[str] = field(default_factory=list)
    dropped_provisions: list[int] = field(default_factory=list)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _provisions(
    judgment: Mapping[str, Any],
    store: TextStore,
    key: str,
    failures: set[int],
    built: Built,
) -> tuple[list[dict[str, Any]], dict[str, Mapping[str, Any]]]:
    """Provisions whose quote is found in a stored version, each cited to that version's
    URL; and the earlier versions cited ({url: metadata})."""
    latest, _ = store.for_key(key)
    versions = store.texts_for_key(key)
    out = []
    earlier: dict[str, Mapping[str, Any]] = {}
    for i, p in enumerate(judgment.get("provisions") or []):
        found = (
            None
            if i in failures
            else next((m for m, t in versions if quote_in_text(p["quote"], t)), None)
        )
        if found is None or not found.get("url"):
            built.dropped_provisions.append(i)
            continue
        if found.get("doc_id") != (latest or {}).get("doc_id"):
            earlier[found["url"]] = found
        prov: dict[str, Any] = {"type": p["type"], "description": p["description"].strip()}
        if p.get("education_levels"):
            prov["education_levels"] = list(p["education_levels"])
        prov["citation"] = {
            "source_url": found["url"],
            "locator": p["locator"].strip(),
            "quote": _clean(p["quote"]),
        }
        out.append(prov)
    return out, earlier


def _verification(
    judgment: Mapping[str, Any],
    store: TextStore,
    key: str,
    provisions: list[dict[str, Any]],
    built: Built,
    failures: set[int],
    *,
    method: str,
    notes: list[str],
    accessed: str,
) -> dict[str, Any]:
    meta, text = store.for_key(key)
    has_text = bool(store.texts_for_key(key))
    if text is None:
        reason = (meta or {}).get("reason") or "text not fetched"
        notes.append(f"Text unavailable ({reason}); classified from metadata.")
    if built.dropped_provisions:
        notes.append(
            f"{len(built.dropped_provisions)} provision(s) dropped: quote not verified in the text."
        )
    if has_text and not judgment.get("provisions"):
        notes.append("No quoted provisions.")
    verified = has_text and not failures and bool(provisions) and not built.dropped_provisions
    out: dict[str, Any] = {"status": "machine-verified" if verified else "unverified"}
    if verified:
        out["last_verified"] = accessed
        out["method"] = method
    out["notes"] = " ".join(notes)
    return out


def _chamber(name: str) -> str | None:
    if name.startswith("Rep.") or name.startswith("Del.") or name.startswith("Resident"):
        return "lower"
    if name.startswith("Sen."):
        return "upper"
    return None


def map_sponsors(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for s in row.get("sponsors") or []:
        name = (s.get("name") or "").strip()
        if not name or s.get("withdrawn_date"):
            continue
        entry: dict[str, Any] = {"name": name, "primary": s.get("role") == "sponsor"}
        if s.get("party"):
            entry["party"] = str(s["party"])
        if chamber := _chamber(name):
            entry["chamber"] = chamber
        out.append(entry)
    return out


def map_actions(row: Mapping[str, Any], today: date) -> list[dict[str, Any]]:
    out = []
    for a in actions(row):
        if a["date"] > today.isoformat():
            continue
        entry: dict[str, Any] = {"date": a["date"], "description": a["text"]}
        if a.get("actor"):
            entry["actor"] = a["actor"]
        entry["classification"] = list(a.get("classification") or ["other"])
        out.append(entry)
    return out


def map_versions(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for v in order_versions(row.get("texts") or []):
        url = v.get("url") or v.get("pdf_url")
        if not url:
            continue
        entry: dict[str, Any] = {"note": v.get("type") or "Text"}
        if v.get("date"):
            entry["date"] = v["date"]
        entry["url"] = url
        entry["media_type"] = "text/html" if url == v.get("url") else "application/pdf"
        out.append(entry)
    return out


def build_bill(
    judgment: Mapping[str, Any],
    row: Mapping[str, Any],
    store: TextStore,
    *,
    today: date,
    quote_failures: set[int],
) -> Built:
    key = judgment["key"]
    congress, slug = str(row["congress"]), f"{row['type']}-{row['number']}".lower()
    built = Built(key, None, Path("legislation") / "us" / congress / f"{slug}.yaml")
    bill_url = row.get("url")
    if not bill_url:
        built.problems.append("no Congress.gov URL for the bill; not imported")
        return built
    acts = map_actions(row, today)
    if not acts:
        built.problems.append("no recorded actions; not imported")
        return built
    provisions, earlier = _provisions(judgment, store, key, quote_failures, built)
    accessed = today.isoformat()
    identifier = row["identifier"]
    sources = [
        {
            "url": bill_url,
            "title": f"{identifier} ({ordinal(int(congress))} Congress)",
            "publisher": "Congress.gov",
            "kind": "official",
            "accessed": accessed,
        }
    ]
    meta, _ = store.for_key(key)
    cited = [m for m in [meta, *earlier.values()] if m and m.get("url")]
    for m in cited:
        if m["url"] != bill_url and m["url"] not in {s["url"] for s in sources}:
            sources.append(
                {
                    "url": m["url"],
                    "title": f"{identifier} text ({m.get('type') or 'version'})",
                    "publisher": "Congress.gov",
                    "kind": "official",
                    "accessed": accessed,
                }
            )
    notes = [BILL_NOTE]
    if earlier:
        kinds = sorted({str(m.get("type") or "earlier") for m in earlier.values()})
        notes.append(f"Some provisions quote an earlier version ({', '.join(kinds)}).")
    record: dict[str, Any] = {
        "id": f"us-{congress}-{slug}",
        "jurisdiction": "us",
        "session": congress,
        "identifier": identifier,
        "title": (row.get("title") or identifier).strip(),
        "classification": row.get("classification") or "bill",
        "education_levels": list(judgment["education_levels"]),
        "topics": list(judgment["topics"]),
        "summary": _clean(judgment["summary"]),
    }
    if provisions:
        record["provisions"] = provisions
    if sponsors := map_sponsors(row):
        record["sponsors"] = sponsors
    record["actions"] = acts
    if versions := map_versions(row):
        record["versions"] = versions
    record["external_ids"] = {"congress_gov": f"{congress}/{row['type']}/{row['number']}".lower()}
    record["sources"] = sources
    record["verification"] = _verification(
        judgment,
        store,
        key,
        provisions,
        built,
        quote_failures,
        method=BILL_METHOD,
        notes=notes,
        accessed=accessed,
    )
    built.record = record
    return built


def _fr_document(cp: ClassifyPaths, number: str) -> dict[str, Any]:
    """The cached Federal Register API response for a document, or {}."""
    path = cp.federal_register / "documents" / f"{number}.json"
    return read_json(path).get("data") or {} if path.exists() else {}


def eo_status(notes: str | None) -> tuple[str, str | None]:
    """(status, end date) from the Federal Register's executive order notes."""
    text = notes or ""
    for pattern, status in ((r"\bRevoked by\b", "rescinded"), (r"\bSuperseded by\b", "superseded")):
        m = re.search(pattern + r"[^\n]*", text, re.IGNORECASE)
        if m:
            d = _NOTE_DATE.search(m.group(0))
            ended = (
                datetime.strptime(f"{d.group(1)} {d.group(2)} {d.group(3)}", "%B %d %Y")
                .date()
                .isoformat()
                if d
                else None
            )
            return status, ended
    return "active", None


def build_fr(
    cp: ClassifyPaths,
    judgment: Mapping[str, Any],
    row: Mapping[str, Any],
    store: TextStore,
    *,
    today: date,
    quote_failures: set[int],
) -> Built:
    key = judgment["key"]
    kind = record_kind(row)
    number = str(row["document_number"])
    slug = key.removeprefix("us-")
    folder = "executive-actions" if kind == EXECUTIVE_ACTION else "documents"
    built = Built(key, None, Path(folder) / "us" / f"{slug}.yaml")
    url = row.get("html_url")
    if not url:
        built.problems.append("no federalregister.gov URL for the document; not imported")
        return built
    provisions, _ = _provisions(judgment, store, key, quote_failures, built)
    accessed = today.isoformat()
    citation = f" ({row['citation']})" if row.get("citation") else ""
    notes = [FR_NOTE]
    if kind == EXECUTIVE_ACTION:
        title = f"Executive Order {row['executive_order_number']}, {row['title']}{citation}"
    else:
        title = f"{row['title']}{citation}, FR Doc. {number}"
    sources = [
        {
            "url": url,
            "title": title,
            "publisher": "Federal Register",
            "kind": "official",
            "accessed": accessed,
        }
    ]
    common = {
        "education_levels": list(judgment["education_levels"]),
        "topics": list(judgment["topics"]),
        "summary": _clean(judgment["summary"]),
    }
    if kind == EXECUTIVE_ACTION:
        if not row.get("signing_date"):
            built.problems.append("no signing date from the Federal Register; not imported")
            return built
        doc = _fr_document(cp, number)
        status, ended = eo_status(doc.get("executive_order_notes"))
        if doc:
            notes.append(
                "Status from the Federal Register's executive order notes"
                + ("." if status != "active" else ", which record no revocation.")
            )
        else:
            notes.append("Status not checked: no Federal Register executive order notes cached.")
        record: dict[str, Any] = {
            "id": key,
            "jurisdiction": "us",
            "issuer": "us-president",
            "number": f"EO {row['executive_order_number']}",
            "title": row["title"].strip(),
            "classification": "executive-order",
            "status": status,
            "signed": row["signing_date"],
        }
        if ended:
            record["ended"] = ended
        record.update(common)
        if provisions:
            record["provisions"] = provisions
        record["external_ids"] = {"federal_register_document_number": number}
    else:
        slugs = [a.get("slug") for a in row.get("agencies") or []]
        issuer = next((AGENCY_ORGS[s] for s in slugs if s in AGENCY_ORGS), None)
        if issuer is None:
            built.problems.append(
                f"no organization record for agency {', '.join(str(s) for s in slugs)}; "
                "not imported"
            )
            return built
        if not judgment.get("classification"):
            built.problems.append("no document classification in the judgment; not imported")
            return built
        record = {
            "id": key,
            "jurisdiction": "us",
            "issuer": issuer,
            "title": row["title"].strip(),
            "classification": judgment["classification"],
            "status": "current",
            **common,
        }
        if row.get("publication_date"):
            record["published"] = row["publication_date"]
        if row.get("effective_on"):
            record["effective"] = row["effective_on"]
        if provisions:
            record["provisions"] = provisions
        notes.append("Status not checked against later Federal Register documents.")
    record["sources"] = sources
    record["verification"] = _verification(
        judgment,
        store,
        key,
        provisions,
        built,
        quote_failures,
        method=FR_METHOD,
        notes=notes,
        accessed=accessed,
    )
    built.record = record
    return built


def import_federal(
    cp: ClassifyPaths,
    target: Paths,
    report: CheckReport,
    candidates: Mapping[str, Mapping[str, Any]],
    *,
    today: date,
    overwrite: bool = False,
    dry_run: bool = False,
    strict_quotes: bool = False,
) -> ImportResult:
    """Write records for every accepted in-scope federal judgment into target/data."""
    result = ImportResult()
    store = TextStore(cp.texts)
    for checked in report.items:
        row = checked.row
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
        if cand.get("existing_record") and not overwrite:
            result.skipped.append((checked.key, f"already recorded as {cand['existing_record']}"))
            continue
        failures = set(checked.quote_failures)
        if cand.get("source") == "congress":
            built = build_bill(row, cand, store, today=today, quote_failures=failures)
        else:
            built = build_fr(cp, row, cand, store, today=today, quote_failures=failures)
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
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dump(built.record), encoding="utf-8")
        result.written.append(path)
    return result
