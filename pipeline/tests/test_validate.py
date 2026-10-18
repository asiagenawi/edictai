import shutil
from pathlib import Path

import pytest
import yaml

from statescope.records import load_records
from statescope.validate import bill_slug, validate
from statescope.vocab import Vocab

from .conftest import FIXTURES, REPO, TODAY, make_tree

CASES = sorted(p for p in (FIXTURES / "invalid").iterdir() if p.is_dir())


def test_valid_tree_is_clean(tree):
    report, records = validate(tree, today=TODAY)
    assert report.issues == [], "\n".join(i.format() for i in report.issues)
    assert len(records) == 11


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_invalid_case(case: Path, tmp_path):
    expect = yaml.safe_load((case / "expect.yaml").read_text())
    severity = expect.get("severity", "error")
    report, _ = validate(make_tree(tmp_path, case), today=TODAY)
    matches = [
        i
        for i in report.issues
        if i.rule == expect["rule"]
        and i.severity == severity
        and ("pointer" not in expect or i.pointer == expect["pointer"])
        and ("file" not in expect or i.file == expect["file"])
        and expect.get("message", "") in i.message
    ]
    assert matches, "expected issue not reported; got:\n" + "\n".join(
        i.format() for i in report.issues
    )
    if severity == "warning":
        assert report.ok, "\n".join(i.format() for i in report.errors)
    else:
        assert not report.ok


def test_missing_organization_is_error_when_strict(tmp_path):
    paths = make_tree(tmp_path, FIXTURES / "invalid" / "missing-organization")
    report, _ = validate(paths, strict=True, today=TODAY)
    assert [i.rule for i in report.errors] == ["ref"]
    assert report.errors[0].pointer == "/issuer"


def test_partial_tree_with_only_reference_data(tmp_path):
    paths = make_tree(tmp_path)
    for kind in ("legislation", "documents", "executive-actions", "research", "organizations"):
        shutil.rmtree(paths.data / kind)
    report, records = validate(paths, today=TODAY)
    assert report.ok
    assert len(records) == 3


def test_issue_formats(tmp_path):
    report, _ = validate(make_tree(tmp_path, FIXTURES / "invalid" / "unknown-topic"), today=TODAY)
    issue = report.errors[0]
    assert issue.format().startswith(
        "data/legislation/us-tx/2025/sb-20.yaml#/topics/0: error [vocab]"
    )
    assert issue.github().startswith(
        "::error file=data/legislation/us-tx/2025/sb-20.yaml,title=vocab::"
    )


def test_timestamps_stay_strings(tree):
    records = load_records(tree.data)
    bill = next(r for r in records if r.id == "us-tx-2025-hb-1481")
    assert bill.data["actions"][0]["date"] == "2025-02-10"


def test_real_vocab_is_consistent():
    vocab = Vocab.load(REPO / "data" / "vocab")
    topics = vocab.codes("topics")
    assert all(p["topic"] in topics for p in vocab.provision_types)


def test_real_schemas_only_name_known_vocabularies():
    vocab = Vocab.load(REPO / "data" / "vocab")
    names = set()
    for path in (REPO / "data" / "schema").glob("*.json"):
        text = path.read_text()
        names.update(part.split('"')[0] for part in text.split('"x-vocab": "')[1:])
    assert names
    assert all(vocab.has(n) for n in names), names


@pytest.mark.parametrize(
    ("identifier", "slug"),
    [
        ("HB 1481", "hb-1481"),
        ("H.R. 610", "hr-610"),
        ("S. 321", "s-321"),
        ("H.J.R. 5", "hjr-5"),
        ("SB20", "sb-20"),
        ("AB 2876", "ab-2876"),
        ("1234", None),
    ],
)
def test_bill_slug(identifier, slug):
    assert bill_slug(identifier) == slug
