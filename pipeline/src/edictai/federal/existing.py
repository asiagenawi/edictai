"""Index the federal records already in `data/` so candidates can be flagged.

Bills match on `external_ids.congress_gov` ("119/hr/610"), falling back to
the record's path (`legislation/us/119/hr-610.yaml`). Federal Register
documents match on `external_ids.federal_register_document_number`, on the
document number in the record's first source URL, or, for executive orders,
on the order number (`number: EO 14277`). Later sources are ignored because
records cite related documents there (an order's rescission, for example).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..yamlio import load_yaml

_FR_URL = re.compile(
    r"federalregister\.gov/(?:documents|d)/(?:\d{4}/\d{2}/\d{2}/)?(\d{4}-\d{4,6})\b"
    r"|govinfo\.gov/content/pkg/FR-[\d-]+/(?:pdf|html)/(\d{4}-\d{4,6})\."
)
_EO = re.compile(r"^\s*(?:EO|E\.O\.|Executive Order)\s*(?:No\.\s*)?(\d+)\s*$", re.IGNORECASE)
_BILL_PATH = re.compile(r"^([a-z]+)-(\d+)$")


def fr_document_number(url: str | None) -> str | None:
    if not url:
        return None
    m = _FR_URL.search(url)
    return (m.group(1) or m.group(2)) if m else None


@dataclass
class ExistingIndex:
    bills: dict[tuple[int, str, str], str] = field(default_factory=dict)
    fr_documents: dict[str, str] = field(default_factory=dict)
    executive_orders: dict[str, str] = field(default_factory=dict)

    def match_bill(self, congress: int, bill_type: str, number: str | int) -> str | None:
        return self.bills.get((int(congress), bill_type.lower(), str(number)))

    def match_document(self, document_number: str | None, eo_number: str | None) -> str | None:
        if document_number and document_number in self.fr_documents:
            return self.fr_documents[document_number]
        if eo_number and str(eo_number) in self.executive_orders:
            return self.executive_orders[str(eo_number)]
        return None


def _load(path: Path) -> Mapping[str, Any] | None:
    try:
        data = load_yaml(path)
    except Exception:  # an unreadable record is the validator's problem, not ours
        return None
    return data if isinstance(data, dict) else None


def _first_source_url(record: Mapping[str, Any]) -> str | None:
    sources = record.get("sources") or []
    if sources and isinstance(sources[0], dict):
        return sources[0].get("url")
    return None


def build_index(data_dir: Path, jurisdiction: str = "us") -> ExistingIndex:
    index = ExistingIndex()
    for path in sorted((data_dir / "legislation" / jurisdiction).glob("*/*.yaml")):
        record = _load(path)
        if record is None:
            continue
        rid = record.get("id") or path.stem
        ext = (record.get("external_ids") or {}).get("congress_gov")
        if isinstance(ext, str) and ext.count("/") == 2:
            congress, bill_type, number = ext.split("/")
            if congress.isdigit():
                index.bills[(int(congress), bill_type.lower(), number)] = rid
        m = _BILL_PATH.match(path.stem)
        if m and path.parent.name.isdigit():
            index.bills.setdefault((int(path.parent.name), m.group(1), m.group(2)), rid)
    for kind in ("executive-actions", "documents"):
        for path in sorted((data_dir / kind / jurisdiction).glob("*.yaml")):
            record = _load(path)
            if record is None:
                continue
            rid = record.get("id") or path.stem
            numbers = {
                (record.get("external_ids") or {}).get("federal_register_document_number"),
                fr_document_number(_first_source_url(record)),
            }
            for n in numbers - {None}:
                index.fr_documents.setdefault(str(n), rid)
            m = _EO.match(str(record.get("number") or ""))
            if m:
                index.executive_orders.setdefault(m.group(1), rid)
    return index
