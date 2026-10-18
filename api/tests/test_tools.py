import json

import pytest

from services.ask_tools import TurnContext, run_tool

AB = 'us-ca-2025-2026-ab-1159'
SB = 'us-ca-2023-2024-sb-1288'


def call(name, args, ctx=None):
    ctx = ctx or TurnContext()
    text, is_error = run_tool(ctx, name, args)
    return json.loads(text), is_error, ctx


def test_search_filters_and_registers_ids(env):
    out, err, ctx = call('search_records', {'query': 'pupil data', 'jurisdiction': 'California'})
    assert not err
    assert [r['id'] for r in out['results']] == [AB]
    assert ctx.returned_ids == {AB}


def test_search_by_bill_number(env):
    out, _, _ = call('search_records', {'query': 'SB1288'})
    assert out['results'][0]['id'] == SB
    out, _, _ = call('search_records', {'query': 'SB 1288'})
    assert out['results'][0]['id'] == SB


def test_search_exact_filters(env):
    out, _, _ = call('search_records', {'record_type': 'executive-action'})
    assert [r['id'] for r in out['results']] == ['us-eo-14277']
    out, _, _ = call('search_records', {'jurisdiction': 'us-ca', 'status': 'current'})
    assert [r['id'] for r in out['results']] == ['us-ca-cde-ai-guidance']
    out, _, _ = call('search_records', {'jurisdiction': 'CA', 'since': '2025', 'record_type': 'legislation'})
    assert [r['id'] for r in out['results']] == [AB]
    out, _, _ = call('search_records', {'topic': 'student-privacy'})
    assert [r['id'] for r in out['results']] == [AB]


def test_search_injection_is_inert(env):
    out, err, _ = call('search_records', {'query': '"; DROP TABLE meta; -- NEAR( OR *'})
    assert not err
    out, _, _ = call('search_records', {'query': 'pupil'})
    assert out['results']


def test_bad_jurisdiction_is_tool_error(env):
    out, err, _ = call('search_records', {'jurisdiction': 'Atlantis'})
    assert err and 'unknown jurisdiction' in out['error']


def test_get_record_and_related(env):
    out, err, ctx = call('get_record', {'id': SB})
    assert not err
    assert out['actions'][-1]['description'].startswith('Approved')
    assert out['provisions'][0]['type'] == 'creates-task-force'
    assert out['related'][0]['id'] == 'us-ca-cde-ai-guidance'
    assert {SB, 'us-ca-cde-ai-guidance'} <= ctx.returned_ids


def test_get_unknown_record(env):
    out, _, ctx = call('get_record', {'id': 'nope'})
    assert 'error' in out and not ctx.returned_ids


def test_provision_grid(env):
    out, _, ctx = call('provision_grid', {'provision_type': 'creates-task-force'})
    assert {g['jurisdiction'] for g in out['grid']} == {'us', 'us-ca'}
    assert SB in ctx.returned_ids
    out, _, _ = call('provision_grid', {})
    assert {p['provision_type'] for p in out['provision_types']} == {'creates-task-force', 'restricts-student-data-use'}


def test_state_overview(env):
    out, _, _ = call('state_overview', {'jurisdiction': 'CA'})
    assert out['counts']['legislation'] == {'enacted': 2}
    assert [d['id'] for d in out['current_documents']] == ['us-ca-cde-ai-guidance']


def test_recent_changes(env):
    out, _, _ = call('recent_changes', {'since': '2026-01-01'})
    assert out['changes'][0]['record_id'] == AB


def test_stats_fixed_aggregates(env):
    out, _, _ = call('stats', {'measure': 'enacted_legislation', 'group_by': 'year'})
    assert {g['group']: g['count'] for g in out['groups']} == {'2024': 1, '2025': 1}
    out, err, _ = call('stats', {'measure': 'DELETE FROM meta', 'group_by': 'year'})
    assert err


@pytest.mark.parametrize('text,expected', [
    ('AB 1159 and S. 2740', {'AB1159', 'S2740'}),
    ('H.R. 6791 passed', {'HR6791'}),
    ('Executive Order 14110', {'EO14110'}),
    ('Senate Bill 1288', {'SB1288'}),
    ('in the U.S. 2025 session', set()),
])
def test_identifier_detection(text, expected):
    from services.ask_agent import mentioned_identifiers

    assert mentioned_identifiers(text) == expected
