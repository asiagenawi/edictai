"""Fixtures that assemble a throwaway repository around the fixture records.

The schemas, vocabularies, and db/schema.sql are copied from the real
repository rather than duplicated under fixtures/, so the tests always run
against the definitions the pipeline ships with.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from statescope.paths import Paths

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 17)


def make_tree(dest: Path, *overlays: Path) -> Paths:
    data = dest / "data"
    shutil.copytree(REPO / "data" / "schema", data / "schema")
    shutil.copytree(REPO / "data" / "vocab", data / "vocab")
    shutil.copytree(FIXTURES / "valid", data, dirs_exist_ok=True)
    for overlay in overlays:
        shutil.copytree(
            overlay, data, dirs_exist_ok=True, ignore=shutil.ignore_patterns("expect.yaml")
        )
    (dest / "db").mkdir()
    shutil.copy(REPO / "db" / "schema.sql", dest / "db" / "schema.sql")
    return Paths(dest)


@pytest.fixture
def tree(tmp_path: Path) -> Paths:
    return make_tree(tmp_path)
