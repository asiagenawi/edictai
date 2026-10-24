"""The tools against the database `edictai build` makes from data/.

Skipped when build/edictai.db has not been built (run `edictai build`).
"""

import json
import os

import pytest

from conftest import REPO_DIR

BUILT_DB = os.path.join(REPO_DIR, 'build', 'edictai.db')
pytestmark = pytest.mark.skipif(not os.path.exists(BUILT_DB), reason='build/edictai.db not built')


@pytest.fixture
def built(monkeypatch):
    import config
    from services import ask_tools, search_index

    monkeypatch.setattr(config, 'DATABASE_PATH', BUILT_DB)
    search_index.reset_index()
    monkeypatch.setattr(ask_tools, '_DEFINITIONS', None)
    yield search_index.get_index()
    search_index.reset_index()


def call(name, args):
    from services.ask_tools import TurnContext, run_tool

    ctx = TurnContext()
    text, is_error = run_tool(ctx, name, args)
    assert not is_error, text
    return json.loads(text), ctx


def test_index_holds_every_record(built):
    counts = dict(built.conn.execute('SELECT record_type, COUNT(*) FROM meta GROUP BY record_type').fetchall())
    # The build has at least the records it had when Ask was rebuilt (2026-10-18).
    assert counts['legislation'] >= 577
    assert counts['document'] >= 67
    assert counts['executive-action'] >= 12
    assert counts['research'] >= 50
    assert built.data_as_of


def test_every_record_type_is_searchable(built):
    for record_type in ('legislation', 'document', 'executive-action', 'research'):
        out, ctx = call('search_records', {'query': 'artificial intelligence', 'record_type': record_type})
        assert out['results'], record_type
        assert all(r['type'] == record_type for r in out['results'])
        assert ctx.returned_ids


def test_bill_number_lookup(built):
    out, _ = call('search_records', {'query': 'AB 1159', 'jurisdiction': 'California'})
    assert out['results'][0]['id'] == 'us-ca-2025-2026-ab-1159'
    rec, ctx = call('get_record', {'id': 'us-ca-2025-2026-ab-1159'})
    assert rec['provisions'] and rec['sources']
    assert 'us-ca-2025-2026-ab-1159' in ctx.returned_ids


def test_which_states_grid(built):
    out, ctx = call('provision_grid', {'provision_type': 'requires-local-ai-policy'})
    assert out['jurisdictions_with_provision'] > 5
    assert ctx.returned_ids


def test_state_overview_and_research(built):
    out, _ = call('state_overview', {'jurisdiction': 'Wyoming'})
    assert out['name'] == 'Wyoming'
    out, _ = call('search_research', {'query': 'teachers using AI'})
    assert out['results'] and out['results'][0]['key_findings']
    assert out['results'][0].get('publisher')


def test_stats_and_recent_changes(built):
    out, _ = call('stats', {'measure': 'enacted_legislation', 'group_by': 'jurisdiction'})
    assert out['total'] > 0
    out, _ = call('recent_changes', {'since': '2026-01'})
    assert out['changes']
