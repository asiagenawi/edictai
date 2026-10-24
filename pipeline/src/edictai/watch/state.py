"""Watcher state kept under .cache/watch/ (never committed).

state.json holds, per URL: the latest hash, conditional-request validators,
a history of observed hashes, a log of change entries, and, per URL, the hash
`watch-apply` last brought the records up to date with. The last normalized
text and link list for each URL are stored beside it so the next change can be
diffed, and so is the text as it stood when the records were last brought up
to date (the base), so `watch-apply` sees every change since then at once.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VERSION = 1


def url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


@dataclass
class WatchState:
    directory: Path
    urls: dict[str, dict[str, Any]] = field(default_factory=dict)
    changes: list[dict[str, Any]] = field(default_factory=list)
    # Per host: last page fetch (epoch seconds) and robots.txt Crawl-delay, so
    # the delay holds across runs as well as within one.
    hosts: dict[str, dict[str, float]] = field(default_factory=dict)
    # Per URL: the hash, date, and verdict of the last `watch-apply` review.
    applied: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.directory / "state.json"

    @property
    def pages(self) -> Path:
        return self.directory / "pages"

    @classmethod
    def load(cls, directory: Path) -> WatchState:
        state = cls(directory)
        if state.path.exists():
            data = json.loads(state.path.read_text(encoding="utf-8"))
            state.urls = data.get("urls", {})
            state.changes = data.get("changes", [])
            state.hosts = data.get("hosts", {})
            state.applied = data.get("applied", {})
        return state

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        payload = {
            "version": VERSION,
            "urls": self.urls,
            "changes": self.changes,
            "hosts": self.hosts,
            "applied": self.applied,
        }
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def entry(self, url: str) -> dict[str, Any]:
        return self.urls.setdefault(url, {"history": []})

    # Snapshot of the last extracted text and links -----------------------
    def read_text(self, url: str) -> str | None:
        p = self.pages / f"{url_key(url)}.txt"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def read_links(self, url: str) -> list[dict[str, str]] | None:
        p = self.pages / f"{url_key(url)}.links.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    # Text as of the last `watch-apply` review --------------------------------
    def read_base(self, url: str) -> str | None:
        p = self.pages / f"{url_key(url)}.base.txt"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def has_base(self, url: str) -> bool:
        return (self.pages / f"{url_key(url)}.base.txt").exists()

    def write_base(self, url: str, text: str | None) -> None:
        self.pages.mkdir(parents=True, exist_ok=True)
        p = self.pages / f"{url_key(url)}.base.txt"
        if text is None:
            p.unlink(missing_ok=True)
        else:
            p.write_text(text, encoding="utf-8")

    def write_snapshot(self, url: str, text: str | None, links: list[dict[str, str]]) -> None:
        self.pages.mkdir(parents=True, exist_ok=True)
        base = self.pages / url_key(url)
        txt = base.with_suffix(".txt")
        if text is None:
            txt.unlink(missing_ok=True)
        else:
            txt.write_text(text, encoding="utf-8")
        base.with_suffix(".links.json").write_text(
            json.dumps(links, indent=1) + "\n", encoding="utf-8"
        )
