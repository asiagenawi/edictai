"""Shared fixtures: a small database built from db/schema.sql and a scripted Claude client."""

import json
import os
import sqlite3
import sys
from types import SimpleNamespace

import pytest

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(API_DIR)
sys.path.insert(0, API_DIR)

FIXTURE_SQL = """
INSERT INTO records VALUES
  ('us', 'jurisdiction', 'data/jurisdictions/us.yaml', 1),
  ('us-ca', 'jurisdiction', 'data/jurisdictions/us-ca.yaml', 2),
  ('us-tx', 'jurisdiction', 'data/jurisdictions/us-tx.yaml', 3),
  ('us-ca-2023-2024-sb-1288', 'legislation', 'data/legislation/us-ca/2023-2024/sb-1288.yaml', 10),
  ('us-ca-2025-2026-ab-1159', 'legislation', 'data/legislation/us-ca/2025-2026/ab-1159.yaml', 11),
  ('us-ca-cde-ai-guidance', 'document', 'data/documents/us-ca/cde-ai-guidance.yaml', 12),
  ('us-eo-14277', 'executive-action', 'data/executive-actions/us/eo-14277.yaml', 13);

INSERT INTO jurisdictions VALUES
  ('us', 'ocd-division/country:us', 'United States', 'US', 'country', NULL, NULL, NULL),
  ('us-ca', 'ocd-division/country:us/state:ca', 'California', 'CA', 'state', '06', 'West', NULL),
  ('us-tx', 'ocd-division/country:us/state:tx', 'Texas', 'TX', 'state', '48', 'Southwest', NULL);

INSERT INTO sessions (jurisdiction, identifier, name, start_date, end_date) VALUES
  ('us-ca', '2023-2024', '2023-2024 Regular Session', '2022-12-05', '2024-11-30'),
  ('us-ca', '2025-2026', '2025-2026 Regular Session', '2024-12-02', NULL);

INSERT INTO topics VALUES
  ('ai-literacy', 'AI literacy', NULL, 1, 1, 1),
  ('student-privacy', 'Student privacy', NULL, 2, 1, 0);
INSERT INTO provision_types VALUES
  ('creates-task-force', 'Creates a task force, council, or study', 'ai-literacy', 1, 1, 1),
  ('restricts-student-data-use', 'Restricts AI vendors'' use of student data', 'student-privacy', 2, 1, 0);

INSERT INTO legislation (id, jurisdiction, session, identifier, title, classification, summary,
    status, status_date, introduced_date, enacted_date, last_action_date, verification_status, last_verified)
VALUES
  ('us-ca-2023-2024-sb-1288', 'us-ca', '2023-2024', 'SB 1288', 'Public schools: artificial intelligence working group.',
   'bill', 'Requires the Superintendent to convene a working group on artificial intelligence in public schools.',
   'enacted', '2024-09-28', '2024-02-15', '2024-09-28', '2024-09-28', 'machine-verified', '2026-10-01'),
  ('us-ca-2025-2026-ab-1159', 'us-ca', '2025-2026', 'AB 1159', 'Student personal information.',
   'bill', 'Limits how operators of AI tools may use pupil data.',
   'enacted', '2026-09-10', '2025-02-20', '2026-09-10', '2026-09-10', 'unverified', '2026-10-17');

INSERT INTO legislation_actions VALUES
  ('us-ca-2023-2024-sb-1288', 0, '2024-02-15', 'upper', 'Introduced.'),
  ('us-ca-2023-2024-sb-1288', 1, '2024-09-28', 'executive', 'Approved by the Governor. Chaptered.'),
  ('us-ca-2025-2026-ab-1159', 0, '2025-02-20', 'lower', 'Introduced.'),
  ('us-ca-2025-2026-ab-1159', 1, '2026-09-10', 'executive', 'Approved by the Governor.');

INSERT INTO documents (id, jurisdiction, title, classification, status, summary, published, undated,
    verification_status, last_verified)
VALUES ('us-ca-cde-ai-guidance', 'us-ca', 'Artificial Intelligence: Learning With AI, Learning About AI',
   'guidance', 'current', 'Guidance from the California Department of Education on AI in schools.',
   '2023-10', 0, 'machine-verified', '2026-09-01');

INSERT INTO executive_actions (id, jurisdiction, number, title, classification, status, signed, summary,
    verification_status, last_verified)
VALUES ('us-eo-14277', 'us', 'EO 14277', 'Advancing Artificial Intelligence Education for American Youth',
   'executive-order', 'active', '2025-04-23', 'Establishes a White House task force on AI education.',
   'machine-verified', '2026-08-01');

INSERT INTO provisions (id, record_id, record_type, seq, type, description, source_url) VALUES
  (1, 'us-ca-2023-2024-sb-1288', 'legislation', 0, 'creates-task-force', 'Creates a working group on AI in schools.', 'https://leginfo.legislature.ca.gov/'),
  (2, 'us-ca-2025-2026-ab-1159', 'legislation', 0, 'restricts-student-data-use', 'Bars use of pupil data to train AI models.', 'https://leginfo.legislature.ca.gov/'),
  (3, 'us-eo-14277', 'executive-action', 0, 'creates-task-force', 'Creates the White House Task Force on AI Education.', 'https://www.federalregister.gov/');
INSERT INTO provision_education_levels VALUES (1, 'k12', 1), (2, 'k12', 1), (3, 'k12', 1);

INSERT INTO record_topics VALUES
  ('us-ca-2023-2024-sb-1288', 'legislation', 'ai-literacy'),
  ('us-ca-2025-2026-ab-1159', 'legislation', 'student-privacy'),
  ('us-ca-cde-ai-guidance', 'document', 'ai-literacy'),
  ('us-eo-14277', 'executive-action', 'ai-literacy');
INSERT INTO record_education_levels VALUES
  ('us-ca-2023-2024-sb-1288', 'legislation', 'k12'),
  ('us-ca-2025-2026-ab-1159', 'legislation', 'k12'),
  ('us-ca-cde-ai-guidance', 'document', 'k12'),
  ('us-eo-14277', 'executive-action', 'k12');

INSERT INTO sources VALUES
  ('us-ca-2023-2024-sb-1288', 'legislation', 0, 'https://leginfo.legislature.ca.gov/sb1288', 'SB 1288', NULL, 'official', '2026-10-01', NULL),
  ('us-ca-2025-2026-ab-1159', 'legislation', 0, 'https://leginfo.legislature.ca.gov/ab1159', 'AB 1159', NULL, 'official', '2026-10-17', NULL),
  ('us-ca-cde-ai-guidance', 'document', 0, 'https://www.cde.ca.gov/ai', NULL, NULL, 'official', '2026-09-01', NULL),
  ('us-eo-14277', 'executive-action', 0, 'https://www.federalregister.gov/eo14277', NULL, NULL, 'official', '2026-08-01', NULL);

INSERT INTO related_records VALUES ('us-ca-2023-2024-sb-1288', 'us-ca-cde-ai-guidance');
"""


@pytest.fixture
def fixture_db(tmp_path):
    path = tmp_path / 'edictai-test.db'
    conn = sqlite3.connect(path)
    with open(os.path.join(REPO_DIR, 'db', 'schema.sql')) as f:
        conn.executescript(f.read())
    conn.executescript(FIXTURE_SQL)
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture
def env(fixture_db, tmp_path, monkeypatch):
    """Point the service at the fixture database and temp files, with fresh caches."""
    import config
    from services import ask_agent, ask_tools, budget, search_index

    monkeypatch.setattr(config, 'DATABASE_PATH', fixture_db)
    monkeypatch.setattr(config, 'ASK_LEADS_FILE', str(tmp_path / 'leads.jsonl'))
    monkeypatch.setattr(config, 'ASK_BUDGET_FILE', str(tmp_path / 'budget.sqlite'))
    monkeypatch.setattr(config, 'ASK_DAILY_BUDGET', 100)
    monkeypatch.setattr(config, 'ASK_RATE_LIMIT_PER_MINUTE', 100)
    monkeypatch.setattr(config, 'ANTHROPIC_API_KEY', 'test-key')
    search_index.reset_index()
    monkeypatch.setattr(ask_tools, '_DEFINITIONS', None)
    monkeypatch.setattr(ask_agent, '_client', None)
    budget.reset_rate_limits()
    yield SimpleNamespace(leads_file=tmp_path / 'leads.jsonl', tmp=tmp_path)
    search_index.reset_index()
    budget.reset_rate_limits()


# ---------------------------------------------------------------------------
# Scripted Claude client
# ---------------------------------------------------------------------------

def tool_use(name, input, id=None):
    return SimpleNamespace(type='tool_use', id=id or f'toolu_{name}_{abs(hash(json.dumps(input, sort_keys=True))) % 10**8}',
                           name=name, input=input)


def tool_turn(*blocks):
    return SimpleNamespace(content=list(blocks), stop_reason='tool_use')


def final(records_text, citations=(), table=None, general_knowledge=None, no_record_subject=None,
          follow_ups=()):
    body = {
        'records_layer': {'text': records_text, 'citations': list(citations),
                          'no_record_subject': no_record_subject},
        'table': table,
        'general_knowledge': general_knowledge,
        'follow_ups': list(follow_ups),
    }
    return SimpleNamespace(content=[SimpleNamespace(type='text', text=json.dumps(body))],
                           stop_reason='end_turn')


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        # Snapshot the messages: the loop keeps appending to the same list.
        self.calls.append({**kwargs, 'messages': list(kwargs['messages'])})
        if not self.script:
            raise AssertionError('Claude was called more times than scripted')
        step = self.script.pop(0)
        return step(kwargs) if callable(step) else step


class FakeClient:
    def __init__(self, script):
        self.beta = SimpleNamespace(messages=FakeMessages(script))

    @property
    def calls(self):
        return self.beta.messages.calls


@pytest.fixture
def claude(env, monkeypatch):
    """Install a scripted client: claude(script) -> FakeClient."""
    from services import ask_agent

    def install(script):
        client = FakeClient(script)
        monkeypatch.setattr(ask_agent, '_client', client)
        return client

    return install


@pytest.fixture
def app_client(env):
    from app import app

    app.config['TESTING'] = True
    return app.test_client()
