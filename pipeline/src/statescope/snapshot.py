"""The static JSON snapshot the web app loads instead of calling the API.

Its shape is the contract web/src/hooks/useSnapshot.js reads: states with a
map status, policies in the legacy field names, topics with integer ids, and a
policy -> topic id mapping. It is produced from the same SQL views the chat API
queries, so the map and the API cannot disagree.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .records import Record

STATE_FIELDS = (
    "id",
    "name",
    "code",
    "fips",
    "region",
    "has_ai_guidance",
    "policy_count",
    "policy_status",
)
POLICY_FIELDS = (
    "id",
    "record_id",
    "record_type",
    "state_id",
    "title",
    "description",
    "policy_type",
    "level",
    "status",
    "date_introduced",
    "date_enacted",
    "bill_number",
    "sponsor",
    "summary_text",
    "source_url",
    "state_code",
    "state_name",
)


def data_updated(records: list[Record]) -> str | None:
    """The month of the most recent verification, as YYYY-MM.

    The interface presents this as the date the data is current to, so it
    follows the last check against sources rather than the build date.
    """
    dates = [
        v["last_verified"]
        for r in records
        if isinstance(v := r.data.get("verification"), dict)
        and isinstance(v.get("last_verified"), str)
    ]
    return max(dates)[:7] if dates else None


def _rows(db: sqlite3.Connection, sql: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    cur = db.execute(sql)
    names = [d[0] for d in cur.description]
    out = []
    for row in cur:
        full = dict(zip(names, row, strict=True))
        out.append({f: full[f] for f in fields})
    return out


def build_snapshot(db: sqlite3.Connection, records: list[Record]) -> dict[str, Any]:
    states = _rows(db, "SELECT * FROM v_states_legacy ORDER BY name", STATE_FIELDS)
    policies = _rows(
        db,
        # SQLite sorts NULL lowest, so undated policies land last, as they did before.
        "SELECT * FROM v_policies_legacy ORDER BY date_introduced DESC, record_id",
        POLICY_FIELDS,
    )
    topics = [
        {"id": pos, "name": name, "description": desc}
        for pos, name, desc in db.execute(
            "SELECT position, name, description FROM topics ORDER BY name"
        )
    ]
    policy_topics: dict[str, list[int]] = {}
    for num, pos in db.execute(
        """
        SELECT r.num_id, t.position
        FROM record_topics rt
        JOIN records r ON r.id = rt.record_id
        JOIN topics t ON t.code = rt.topic
        WHERE rt.record_type IN ('legislation', 'document', 'executive-action')
        ORDER BY r.num_id, t.position
        """
    ):
        policy_topics.setdefault(str(num), []).append(pos)
    return {
        "data_updated": data_updated(records),
        "states": states,
        "policies": policies,
        "topics": topics,
        "policy_topics": policy_topics,
    }


def write(snapshot: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
    )
