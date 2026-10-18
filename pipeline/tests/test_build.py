import csv
import json
import re
import sqlite3

import pytest

from statescope.build import BuildError, build, num_id
from statescope.paths import Paths
from statescope.sqldoc import parse_schema
from statescope.vocab import Vocab

from .conftest import FIXTURES, REPO, TODAY, make_tree


@pytest.fixture
def built(tree: Paths):
    result = build(tree, today=TODAY, log=lambda _: None)
    db = sqlite3.connect(result.db_path)
    yield tree, result, db
    db.close()


def q(db, sql, *params):
    return db.execute(sql, params).fetchall()


def test_integrity(built):
    _, _, db = built
    assert q(db, "PRAGMA foreign_key_check") == []
    assert q(db, "PRAGMA integrity_check") == [("ok",)]


def test_row_counts(built):
    _, result, _ = built
    expected = {
        "jurisdictions": 3,
        "sessions": 2,
        "session_sources": 1,
        "organizations": 2,
        "legislation": 3,
        "legislation_actions": 8,
        "legislation_action_classifications": 9,
        "legislation_sponsors": 3,
        "legislation_versions": 1,
        "documents": 1,
        "document_revisions": 1,
        "executive_actions": 1,
        "research": 1,
        "research_authors": 2,
        "research_findings": 1,
        "research_geography": 1,
        "provisions": 3,
        "records": 11,
        "related_records": 1,
        "external_ids": 3,
        "sources": 6,
        "topics": len(Vocab.load(REPO / "data" / "vocab").topics),
    }
    for table, count in expected.items():
        assert result.counts[table] == count, table


def test_derived_status(built):
    _, _, db = built
    rows = dict(q(db, "SELECT id, status FROM legislation"))
    assert rows == {
        "us-tx-2025-hb-1481": "enacted",
        "us-tx-2025-sb-20": "failed",  # session ended in committee
        "us-119-hr-610": "in-committee",
    }


def test_provision_levels_inherit(built):
    _, _, db = built
    rows = q(
        db,
        """
        SELECT p.record_id, e.education_level, e.inherited
        FROM provisions p JOIN provision_education_levels e ON e.provision_id = p.id
        ORDER BY 1, 2
        """,
    )
    assert rows == [
        ("us-tx-2025-hb-1481", "k12", 1),
        ("us-tx-eo-ga-99", "k12", 0),
        ("us-tx-tea-ai-guidance", "k12", 1),
    ]


def test_new_columns(built):
    _, _, db = built
    assert q(db, "SELECT published, updated, version, effective, undated FROM documents") == [
        ("2025-08", "2026-01", "2.0", None, 0)
    ]
    assert q(db, "SELECT expires FROM executive_actions") == [("2027-09-01",)]
    assert q(
        db,
        "SELECT name, classification FROM legislation_sponsors "
        "WHERE legislation_id = 'us-tx-2025-hb-1481' ORDER BY seq",
    ) == [
        ("Jane Doe", "person"),
        ("Senate Education Committee", "committee"),
        ("John Roe", "person"),
    ]
    assert q(db, "SELECT jurisdiction, session, kind FROM session_sources") == [
        ("us-tx", "2025", "official")
    ]


def test_views(built):
    _, _, db = built
    policies = q(
        db, "SELECT id, record_type, level, is_current, source_url FROM v_policies ORDER BY id"
    )
    assert [p[:4] for p in policies] == [
        ("us-119-hr-610", "legislation", "federal", 0),
        ("us-tx-2025-hb-1481", "legislation", "state", 1),
        ("us-tx-2025-sb-20", "legislation", "state", 0),
        ("us-tx-eo-ga-99", "executive-action", "state", 1),
        ("us-tx-tea-ai-guidance", "document", "state", 1),
    ]
    grid = q(
        db,
        "SELECT records, current_records FROM v_provision_grid WHERE jurisdiction = 'us-tx' "
        "AND provision_type = 'requires-local-ai-policy'",
    )
    assert grid == [(1, 1)]
    total = q(db, "SELECT COUNT(*) FROM v_provision_grid")[0][0]
    assert total == 3 * len(Vocab.load(REPO / "data" / "vocab").provision_types)


def test_snapshot_contract(built):
    tree, result, _ = built
    snap = json.loads((result.out_dir / "snapshot.json").read_text())
    assert set(snap) == {"data_updated", "states", "policies", "topics", "policy_topics"}
    assert snap["data_updated"] == "2026-10"

    states = {s["code"]: s for s in snap["states"]}
    assert set(states) == {"CA", "TX"}  # the federal jurisdiction is not a state
    assert set(states["TX"]) == {
        "id",
        "name",
        "code",
        "fips",
        "region",
        "has_ai_guidance",
        "policy_count",
        "policy_status",
    }
    assert states["TX"]["policy_status"] == "enacted"
    assert states["TX"]["policy_count"] == 4
    assert states["TX"]["has_ai_guidance"] == 1
    assert states["CA"]["policy_status"] == "none"

    by_record = {p["record_id"]: p for p in snap["policies"]}
    hb = by_record["us-tx-2025-hb-1481"]
    assert hb["policy_type"] == "bill"
    assert hb["status"] == "enacted"
    assert hb["bill_number"] == "HB 1481"
    assert hb["sponsor"] == "Jane Doe"
    assert hb["date_introduced"] == "2025-02-10"
    assert hb["date_enacted"] == "2025-06-20"
    assert hb["state_code"] == "TX"
    assert hb["state_name"] == "Texas"
    assert hb["id"] == num_id("us-tx-2025-hb-1481")
    assert by_record["us-tx-2025-sb-20"]["status"] == "failed"
    assert by_record["us-119-hr-610"]["status"] == "introduced"
    assert by_record["us-119-hr-610"]["level"] == "federal"
    assert by_record["us-119-hr-610"]["state_code"] is None
    assert by_record["us-tx-tea-ai-guidance"]["policy_type"] == "guidance"
    assert by_record["us-tx-tea-ai-guidance"]["status"] == "active"
    # A document's date is its current edition when the record gives one.
    assert by_record["us-tx-tea-ai-guidance"]["date_introduced"] == "2026-01"
    eo = by_record["us-tx-eo-ga-99"]
    assert (eo["policy_type"], eo["status"], eo["date_enacted"]) == (
        "executive_order",
        "active",
        "2025-09-01",
    )
    assert eo["sponsor"] == "Office of the Governor of Texas"

    dates = [p["date_introduced"] for p in snap["policies"]]
    assert dates == sorted(dates, reverse=True)

    topic_ids = {t["id"] for t in snap["topics"]}
    assert all(isinstance(t["id"], int) for t in snap["topics"])
    assert set(snap["policy_topics"]) == {str(p["id"]) for p in snap["policies"]}
    assert all(set(v) <= topic_ids for v in snap["policy_topics"].values())

    web = tree.web_snapshot
    assert web.read_bytes() == (result.out_dir / "snapshot.json").read_bytes()


def test_snapshot_uses_only_ui_vocabulary(built):
    # web/src/utils/colors.js and constants.js only know these values.
    _, result, _ = built
    snap = json.loads((result.out_dir / "snapshot.json").read_text())
    assert {s["policy_status"] for s in snap["states"]} <= {
        "none",
        "failed",
        "guidance",
        "pending",
        "enacted",
    }
    assert {p["status"] for p in snap["policies"]} <= {"introduced", "enacted", "failed", "active"}
    assert {p["policy_type"] for p in snap["policies"]} <= {"bill", "guidance", "executive_order"}
    assert {p["level"] for p in snap["policies"]} <= {"federal", "state"}


def test_csv_and_datapackage(built):
    _, result, db = built
    package = json.loads((result.out_dir / "datapackage.json").read_text())
    names = {r["name"] for r in package["resources"]}
    for resource in package["resources"]:
        path = result.out_dir / resource["path"]
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)
        assert header == [field["name"] for field in resource["schema"]["fields"]]
        table = resource["path"].removeprefix("csv/").removesuffix(".csv")
        assert len(rows) == q(db, f"SELECT COUNT(*) FROM {table}")[0][0]
        assert re.fullmatch(r"[a-z0-9-]+", resource["name"])
        for fk in resource["schema"].get("foreignKeys", []):
            assert fk["reference"]["resource"] in names
    legislation = next(r for r in package["resources"] if r["name"] == "legislation")
    status = next(f for f in legislation["schema"]["fields"] if f["name"] == "status")
    assert "failed" in status["constraints"]["enum"]
    assert status["description"]


def test_parquet(built):
    pq = pytest.importorskip("pyarrow.parquet")
    _, result, _ = built
    table = pq.read_table(result.out_dir / "parquet" / "legislation.parquet")
    assert table.num_rows == 3
    assert table.schema.field("status").type == "string"


def test_build_is_deterministic(tree, tmp_path):
    first = build(tree, tmp_path / "a", today=TODAY, write_web=False, log=lambda _: None)
    second = build(tree, tmp_path / "b", today=TODAY, write_web=False, log=lambda _: None)
    for name in ("snapshot.json", "datapackage.json", "csv/legislation.csv", "csv/sources.csv"):
        assert (first.out_dir / name).read_bytes() == (second.out_dir / name).read_bytes(), name


def test_build_stops_on_errors(tmp_path):
    paths = make_tree(tmp_path, FIXTURES / "invalid" / "unknown-topic")
    with pytest.raises(BuildError, match="validation failed"):
        build(paths, today=TODAY, log=lambda _: None)


def test_build_skip_invalid(tmp_path):
    paths = make_tree(tmp_path, FIXTURES / "invalid" / "dangling-related")
    result = build(paths, today=TODAY, skip_invalid=True, write_web=False, log=lambda _: None)
    assert result.counts["legislation"] == 2
    # The guidance's authorized_by pointed at the skipped bill.
    db = sqlite3.connect(result.db_path)
    assert q(db, "SELECT authorized_by FROM documents") == [(None,)]
    assert q(db, "PRAGMA foreign_key_check") == []
    db.close()


def test_missing_organization_builds_with_null_issuer(tmp_path):
    paths = make_tree(tmp_path, FIXTURES / "invalid" / "missing-organization")
    result = build(paths, today=TODAY, write_web=False, log=lambda _: None)
    db = sqlite3.connect(result.db_path)
    assert q(db, "SELECT issuer FROM documents") == [(None,)]
    db.close()
    assert any("us-tx-higher-ed-agency" in n for n in result.notes)


def test_schema_checks_match_vocab():
    """The CHECK constraints in db/schema.sql list exactly the codes in enums.yaml."""
    vocab = Vocab.load(REPO / "data" / "vocab")
    tables = parse_schema(REPO / "db" / "schema.sql")
    expected = {
        ("legislation", "classification"): "legislation_classifications",
        ("legislation", "status"): "legislation_statuses",
        ("legislation", "verification_status"): "verification_statuses",
        ("legislation_actions", "actor"): "action_actors",
        ("legislation_action_classifications", "classification"): "action_classifications",
        ("documents", "classification"): "document_classifications",
        ("documents", "status"): "document_statuses",
        ("documents", "verification_status"): "verification_statuses",
        ("executive_actions", "classification"): "executive_action_classifications",
        ("executive_actions", "status"): "executive_action_statuses",
        ("executive_actions", "verification_status"): "verification_statuses",
        ("research", "classification"): "research_classifications",
        ("research", "verification_status"): "verification_statuses",
        ("organizations", "classification"): "organization_classifications",
        ("sources", "kind"): "source_kinds",
        ("record_education_levels", "education_level"): "education_levels",
        ("provision_education_levels", "education_level"): "education_levels",
    }
    for (table, column), name in expected.items():
        col = tables[table].column(column)
        assert col is not None and col.enum is not None, (table, column)
        assert set(col.enum) == vocab.codes(name), (table, column)


def test_schema_documents_every_column():
    tables = parse_schema(REPO / "db" / "schema.sql")
    db = sqlite3.connect(":memory:")
    db.executescript((REPO / "db" / "schema.sql").read_text())
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        columns = [r[1] for r in db.execute(f"PRAGMA table_info({name})")]
        documented = tables[name]
        assert documented.description, name
        assert [c.name for c in documented.columns] == columns, name
        assert all(c.description for c in documented.columns), name
