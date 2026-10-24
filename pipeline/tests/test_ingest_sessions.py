import json
from datetime import date

import pytest
import yaml

from edictai.ingest.http import HTTPError, RateLimiter
from edictai.ingest.sessions import (
    OpenStatesClient,
    edit_session_yaml,
    jurisdiction_url,
    match_session,
    update_sessions,
)
from edictai.validate import validate
from edictai.yamlio import load_yaml

from .conftest import TODAY

KEY = "os-test-key"

TX_YAML = """\
id: us-tx
ocd_id: "ocd-division/country:us/state:tx"
name: Texas
abbreviation: TX
classification: state
fips: "48"
region: Southwest
sessions:
  - identifier: "2023"
    name: 88th Legislature, Regular Session
    classification: primary
    start_date: "2023-01-10"
  - identifier: "2025"
    name: 89th Legislature, Regular Session
    classification: primary
    start_date: "2025-01-14"
"""

TX_SESSIONS = [
    {
        "identifier": "88",
        "name": "88th Legislature",
        "classification": "primary",
        "start_date": "2023-01-10",
        "end_date": "2023-05-29",
    },
    {
        "identifier": "89",
        "name": "89th Legislature",
        "classification": "primary",
        "start_date": "2025-01-14",
        "end_date": "2025-06-02",
    },
    {
        "identifier": "891",
        "name": "89th Legislature, 1st Called Session",
        "classification": "special",
        "start_date": "2025-07-21",
        "end_date": "2025-08-15",
    },
]


class FakeOpenStates:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, params, headers):
        assert headers == {"X-API-KEY": KEY}
        assert params == {"include": "legislative_sessions"}
        self.calls.append(url)
        jid = url.split("/jurisdictions/")[1]
        if jid not in self.responses:
            raise HTTPError(f"HTTP 404 from {url}")
        return json.dumps(self.responses[jid]).encode()


def os_client(tmp_path, api):
    limiter = RateLimiter(0, sleep=lambda s: None)
    return OpenStatesClient(KEY, tmp_path / "openstates", transport=api, limiter=limiter)


@pytest.fixture
def tx_tree(tree):
    (tree.data / "jurisdictions" / "us-tx.yaml").write_text(TX_YAML)
    return tree


def test_fills_missing_end_date_with_source(tx_tree, tmp_path):
    api = FakeOpenStates(
        {
            "ocd-jurisdiction/country:us/state:tx/government": {
                "name": "Texas",
                "legislative_sessions": TX_SESSIONS,
            }
        }
    )
    result = update_sessions(tx_tree, os_client(tmp_path, api), today=TODAY)
    assert [(c.jurisdiction, c.identifier, c.fields) for c in result.changes] == [
        ("us-tx", "2025", {"end_date": "2025-06-02"})
    ]
    path = tx_tree.data / "jurisdictions" / "us-tx.yaml"
    text = path.read_text()
    # The unused 2023 session is untouched; the rest of the file is verbatim.
    assert text.startswith(TX_YAML.split('  - identifier: "2025"')[0])
    session = load_yaml(path)["sessions"][1]
    assert session["end_date"] == "2025-06-02"
    assert session["sources"] == [
        {
            "url": jurisdiction_url("us-tx"),
            "title": "Open States API, Texas legislative sessions",
            "publisher": "Open States",
            "kind": "secondary",
            "accessed": date.today().isoformat(),
        }
    ]
    assert "end_date" not in load_yaml(path)["sessions"][0]
    assert KEY not in text
    # us.yaml already has both dates, so only Texas was requested.
    assert len(api.calls) == 1
    report, _ = validate(tx_tree, today=date.today())
    assert report.ok, "\n".join(i.format() for i in report.issues)

    # Running again changes nothing and uses the cache.
    again = update_sessions(tx_tree, os_client(tmp_path, api), today=TODAY)
    assert again.changes == [] and again.requests == 0
    assert path.read_text() == text


def test_existing_values_are_never_overwritten(tree, tmp_path):
    before = (tree.data / "jurisdictions" / "us-tx.yaml").read_text()
    api = FakeOpenStates({})
    result = update_sessions(tree, os_client(tmp_path, api), today=TODAY)
    assert result.changes == [] and api.calls == []
    assert (tree.data / "jurisdictions" / "us-tx.yaml").read_text() == before


def test_rejects_implausible_end_dates(tx_tree, tmp_path):
    sessions = [dict(s) for s in TX_SESSIONS]
    sessions[1]["end_date"] = "2025-04-01"  # before the record's 2025-04-15 floor vote
    api = FakeOpenStates(
        {
            "ocd-jurisdiction/country:us/state:tx/government": {
                "name": "Texas",
                "legislative_sessions": sessions,
            }
        }
    )
    result = update_sessions(tx_tree, os_client(tmp_path, api), today=TODAY)
    assert result.changes == []
    assert "precedes a recorded action" in result.skipped[0][2]


def test_future_end_dates_are_never_written(tx_tree, tmp_path):
    api = FakeOpenStates(
        {
            "ocd-jurisdiction/country:us/state:tx/government": {
                "name": "Texas",
                "legislative_sessions": TX_SESSIONS,
            }
        }
    )
    result = update_sessions(
        tx_tree, os_client(tmp_path, api), today=date(2025, 5, 1), dry_run=True
    )
    assert result.changes == [] and "in the future" in result.skipped[0][2]


def test_missing_jurisdiction_is_reported(tx_tree, tmp_path):
    result = update_sessions(tx_tree, os_client(tmp_path, FakeOpenStates({})), today=TODAY)
    assert result.skipped == [("us-tx", "2025", "no Open States jurisdiction")]


def test_match_session():
    two_year = {"identifier": "2025-2026", "classification": "primary"}
    theirs = [
        {"identifier": "104th", "classification": "", "start_date": "2025-01-08"},
        {"identifier": "2025-2026", "classification": "primary", "start_date": "2025-01-09"},
    ]
    assert match_session(two_year, theirs, "us-xx")["identifier"] == "2025-2026"
    by_start = {"identifier": "2025-2026", "start_date": "2025-01-08"}
    assert match_session(by_start, theirs[:1], "us-il")["identifier"] == "104th"
    assert match_session({"identifier": "2027"}, theirs, "us-xx") is None


def test_edit_appends_to_existing_sources():
    text = (
        "sessions:\n"
        "  - identifier: '2025'\n"
        "    name: Regular\n"
        "    start_date: '2025-01-01'\n"
        "    sources:\n"
        "      - url: https://example.org/a\n"
        "        kind: official\n"
        "        accessed: '2026-01-01'\n"
    )
    source = {
        "url": "https://v3.openstates.org/x",
        "title": "T",
        "publisher": "Open States",
        "kind": "secondary",
        "accessed": "2026-10-17",
    }
    out = edit_session_yaml(text, "2025", {"end_date": "2025-05-01"}, source)
    assert "    start_date: '2025-01-01'\n    end_date: '2025-05-01'\n" in out
    data = yaml.safe_load(out)["sessions"][0]
    assert [s["kind"] for s in data["sources"]] == ["official", "secondary"]


def test_illinois_end_dates_are_not_copied(tree, tmp_path):
    il = TX_YAML.replace("us-tx", "us-il").replace("state:tx", "state:il")
    il = il.replace("name: Texas", "name: Illinois").replace("abbreviation: TX", "abbreviation: IL")
    il = il.replace('fips: "48"', 'fips: "17"').replace("Southwest", "Midwest")
    (tree.data / "jurisdictions" / "us-il.yaml").write_text(il)
    record = tree.data / "legislation" / "us-tx" / "2025" / "sb-20.yaml"
    target = tree.data / "legislation" / "us-il" / "2025" / "sb-20.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(
        record.read_text().replace("us-tx", "us-il").replace("issuer: us-il-", "issuer: us-tx-")
    )
    api = FakeOpenStates(
        {
            "ocd-jurisdiction/country:us/state:il/government": {
                "name": "Illinois",
                "legislative_sessions": TX_SESSIONS,
            }
        }
    )
    result = update_sessions(tree, os_client(tmp_path, api), today=TODAY, dry_run=True)
    assert result.changes == []
    assert ("us-il", "2025") in {(j, i) for j, i, _ in result.skipped}
    assert "spring adjournments" in result.skipped[0][2]
