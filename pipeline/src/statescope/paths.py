"""Locations of the data tree and build outputs.

Everything is resolved from a repository root rather than from this file's
location, because the package may be installed non-editably (as on Render),
where the source tree is not next to the installed module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def find_root(start: Path | None = None) -> Path:
    """The nearest ancestor of `start` (default: cwd) that holds data/schema."""
    env = os.environ.get("STATESCOPE_ROOT")
    if env:
        return Path(env).resolve()
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "data" / "schema").is_dir():
            return candidate
    raise FileNotFoundError(
        f"No data/schema directory found in {here} or its parents; "
        "run from the repository or pass --root."
    )


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def schema(self) -> Path:
        return self.data / "schema"

    @property
    def vocab(self) -> Path:
        return self.data / "vocab"

    @property
    def db_schema(self) -> Path:
        return self.root / "db" / "schema.sql"

    @property
    def dictionary(self) -> Path:
        return self.root / "docs" / "data" / "dictionary.md"

    @property
    def cache(self) -> Path:
        """Downloaded API responses; never committed."""
        return self.root / ".cache"

    @property
    def web_snapshot(self) -> Path:
        return self.root / "web" / "public" / "data" / "snapshot.json"

    @classmethod
    def discover(cls, root: str | Path | None = None) -> Paths:
        return cls(Path(root).resolve() if root else find_root())
