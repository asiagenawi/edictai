"""YAML loading tuned to the record format."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_Base = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class RecordLoader(_Base):
    """SafeLoader that leaves timestamps as strings.

    The schemas type every date as an ISO string, and contributors write
    `accessed: 2026-10-17` unquoted. YAML 1.1 would turn that into a
    datetime.date and fail validation for a purely cosmetic reason.
    """


RecordLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for first, resolvers in _Base.yaml_implicit_resolvers.items()
}


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.load(f, Loader=RecordLoader)
