"""JSON response cache shared by the federal clients."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def cache_key(path: str, params: Mapping[str, Any]) -> str:
    """A filename-safe key for a request; never includes secrets passed by the caller."""
    items = sorted((str(k), str(v)) for k, v in params.items())
    digest = hashlib.sha256(json.dumps([path, items]).encode()).hexdigest()[:16]
    stem = path.strip("/").replace("/", "_") or "root"
    return f"{stem}-{digest}.json"


class JsonCache:
    """Stores `{fetched, data}` envelopes under one directory."""

    def __init__(self, root: Path, *, now: Callable[[], datetime] | None = None):
        self.root = root
        self.now = now or (lambda: datetime.now(UTC))

    def file(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def get(self, path: Path, max_age: timedelta | None = None) -> Any | None:
        """The cached data, or None if missing or older than `max_age`."""
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if max_age is not None:
            fetched = datetime.fromisoformat(envelope.get("fetched", "1970-01-01T00:00:00+00:00"))
            if self.now() - fetched > max_age:
                return None
        return envelope.get("data")

    def put(self, path: Path, data: Any, **extra: Any) -> None:
        write_json(path, {"fetched": self.now().isoformat(), "data": data, **extra})

    def meta(self, path: Path) -> dict[str, Any]:
        """The whole envelope, or {} if missing."""
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
