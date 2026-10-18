import json
import logging

from conftest import final, tool_turn, tool_use

QUESTION = 'Which California laws protect pupil data from AI vendors?'
SB = 'us-ca-2023-2024-sb-1288'
AB = 'us-ca-2025-2026-ab-1159'
DOC = 'us-ca-cde-ai-guidance'


def ask(app_client, question=QUESTION, **extra):
    return app_client.post('/api/ask', json={'question': question, **extra})


def search_ab():
    return tool_turn(tool_use('search_records', {'query': 'pupil data', 'jurisdiction': 'CA'}))


# -- citation enforcement ---------------------------------------------------

def test_valid_citation_is_kept_and_rendered(app_client, claude):
    client = claude([
        search_ab(),
        final(f'California enacted AB 1159 [[{AB}]], which limits use of pupil data.', [AB]),
    ])
    res = ask(app_client)
    assert res.status_code == 200
    body = res.get_json()
    assert body['records_layer']['citations'] == [AB]
    assert f'[[{AB}]]' in body['records_layer']['text']
    assert body['records'][AB]['verification'] == 'unverified'
    assert body['no_match'] is False
    assert body['data_as_of'] == '2026-10-17'
    # Legacy fields: Markdown answer with a source link, and a sources list.
    assert '(https://leginfo.legislature.ca.gov/ab1159)' in body['answer']
    assert '(unverified record)' in body['answer']
    assert body['sources'][0]['record_id'] == AB
    assert len(client.calls) == 2


def test_request_uses_cached_system_prompt_and_schema(app_client, claude):
    client = claude([search_ab(), final(f'AB 1159 [[{AB}]].', [AB])])
    ask(app_client)
    kw = client.calls[0]
    assert kw['model'] == 'claude-sonnet-5-5'
    assert kw['system'][0]['cache_control'] == {'type': 'ephemeral'}
    assert kw['output_config']['format']['type'] == 'json_schema'
    assert kw['tool_choice'] == {'type': 'auto'}
    assert {t['name'] for t in kw['tools']} == {
        'search_records', 'get_record', 'provision_grid', 'state_overview',
        'recent_changes', 'search_research', 'stats'}
    # Tool results go back to Claude in the following request.
    second = client.calls[1]['messages']
    assert second[-1]['content'][0]['type'] == 'tool_result'
    assert AB in second[-1]['content'][0]['content']


def test_unknown_id_triggers_one_correction_then_is_stripped(app_client, claude):
    bad = 'us-tx-2025-hb-9999'
    client = claude([
        search_ab(),
        final(f'AB 1159 [[{AB}]] and a Texas bill [[{bad}]].', [AB, bad]),
        final(f'AB 1159 [[{AB}]] and a Texas bill [[{bad}]].', [AB, bad]),
    ])
    body = ask(app_client).get_json()
    # Exactly one corrective round, naming the problem.
    assert len(client.calls) == 3
    correction = client.calls[2]['messages'][-1]['content']
    assert bad in correction and 'not an Edictai record id' in correction
    assert body['records_layer']['citations'] == [AB]
    assert bad not in body['records_layer']['text']
    assert bad not in body['records']


def test_corrected_answer_is_used(app_client, claude):
    claude([
        search_ab(),
        final(f'See [[{SB}]].', [SB]),  # exists, but no tool returned it
        final(f'AB 1159 [[{AB}]] limits pupil data use.', [AB]),
    ])
    body = ask(app_client).get_json()
    assert body['records_layer']['citations'] == [AB]
    assert SB not in body['records']


def test_existing_but_unretrieved_id_is_stripped(app_client, claude):
    client = claude([
        search_ab(),
        final(f'AB 1159 [[{AB}]]; see also [[{DOC}]].', [AB, DOC]),
        final(f'AB 1159 [[{AB}]]; see also [[{DOC}]].', [AB, DOC]),
    ])
    body = ask(app_client).get_json()
    assert 'was not returned by a tool' in client.calls[2]['messages'][-1]['content']
    assert body['records_layer']['citations'] == [AB]
    assert DOC not in body['records']


def test_table_ids_are_filtered(app_client, claude):
    claude([
        search_ab(),
        final(f'One law [[{AB}]].', [AB], table=[AB, 'us-zz-fake']),
        final(f'One law [[{AB}]].', [AB], table=[AB, 'us-zz-fake']),
    ])
    body = ask(app_client).get_json()
    assert body['table'] == [AB]


def test_unsupported_bill_number_falls_back_to_record_list(app_client, claude):
    text = f'AB 1159 [[{AB}]] and also SB 777 restrict vendors.'
    claude([search_ab(), final(text, [AB]), final(text, [AB])])
    body = ask(app_client).get_json()
    assert 'SB 777' not in body['records_layer']['text']
    assert body['records_layer']['text'].startswith('Here are the Edictai records that matched')
    assert AB in body['records_layer']['citations']


def test_answer_without_tool_use_is_sent_back(app_client, claude):
    client = claude([
        final(f'AB 1159 [[{AB}]].', [AB]),
        search_ab(),
        final(f'AB 1159 [[{AB}]].', [AB]),
    ])
    body = ask(app_client).get_json()
    assert 'No tools were consulted' in client.calls[1]['messages'][-1]['content']
    assert body['records_layer']['citations'] == [AB]


# -- no match ---------------------------------------------------------------

def test_no_match_says_no_record_first(app_client, claude):
    claude([
        tool_turn(tool_use('search_records', {'query': 'facial recognition', 'jurisdiction': 'TX'})),
        final('Texas passed a facial recognition ban in 2023.', [], no_record_subject='Texas facial recognition rules',
              general_knowledge={'text': 'Some states have debated facial recognition limits.', 'mentioned_policies': []}),
    ])
    body = ask(app_client, 'Does Texas ban facial recognition in schools?').get_json()
    assert body['no_match'] is True
    assert body['records_layer']['text'] == 'Edictai has no record of Texas facial recognition rules.'
    assert body['records_layer']['citations'] == []
    assert body['suggest_url']
    assert body['searched'] == ['Searching Texas records…']
    # General knowledge follows, separate and labelled.
    assert body['general_knowledge']['verified'] is False
    assert 'not verified by Edictai' in body['general_knowledge']['label']
    md = body['answer']
    assert md.index('Edictai has no record of') < md.index('general knowledge')


def test_no_match_keeps_model_statement_but_nothing_after(app_client, claude):
    claude([
        tool_turn(tool_use('search_records', {'query': 'drones'})),
        final('Edictai has no record of drone policies.\n\nBut Ohio has rules.', []),
    ])
    body = ask(app_client, 'drone rules?').get_json()
    assert body['records_layer']['text'] == 'Edictai has no record of drone policies.'


# -- general knowledge and leads ---------------------------------------------

def gk_answer(policies, text='Texas HB 2 (2025) may address chatbots.'):
    return final(f'California enacted AB 1159 [[{AB}]].', [AB],
                 general_knowledge={'text': text, 'mentioned_policies': policies})


def test_lead_logged_without_question_text(app_client, claude, env):
    question = 'My kid in Austin keeps using chatbots, does Texas regulate this?'
    claude([search_ab(), gk_answer([
        {'jurisdiction': 'Texas', 'identifier': 'HB 2', 'title': 'Chatbot safety for minors',
         'claim': 'Would require age checks for AI companion chatbots.'},
        # Already in the dataset: not a lead.
        {'jurisdiction': 'CA', 'identifier': 'AB 1159', 'title': 'Student personal information.',
         'claim': 'Limits pupil data use.'},
        # Repeats the question: dropped rather than risk storing it.
        {'jurisdiction': 'TX', 'identifier': None, 'title': question, 'claim': 'x'},
    ])])
    body = ask(app_client, question).get_json()
    assert '(unverified)' in body['general_knowledge']['text']
    assert '[[' not in body['general_knowledge']['text']

    raw = env.leads_file.read_text()
    lines = [json.loads(line) for line in raw.splitlines()]
    assert len(lines) == 1
    lead = lines[0]
    assert lead['jurisdiction'] == 'us-tx'
    assert lead['identifier'] == 'HB 2'
    assert set(lead) <= {'jurisdiction', 'identifier', 'title', 'claim', 'seen', 'source'}
    assert question not in raw
    assert 'Austin' not in raw
    assert '127.0.0.1' not in raw


def test_no_lead_file_when_nothing_new(app_client, claude, env):
    claude([search_ab(), gk_answer([], text='Background only.')])
    ask(app_client)
    assert not env.leads_file.exists()


# -- privacy -----------------------------------------------------------------

def test_question_text_never_logged(app_client, claude, caplog):
    question = 'Secret question about Ms. Example at Lincoln Elementary'
    caplog.set_level(logging.DEBUG)

    def boom(kwargs):
        raise RuntimeError(question)  # an error that carries the question

    claude([search_ab(), boom])
    res = ask(app_client, question)
    assert res.status_code == 500
    for record in caplog.records:
        assert 'Lincoln Elementary' not in record.getMessage()
        assert 'Lincoln Elementary' not in (record.exc_text or '')


def test_redaction_filter_scrubs_question():
    from services import privacy

    flt = privacy.RedactQuestionFilter()
    token = privacy.set_question('where is my school')
    try:
        rec = logging.LogRecord('x', logging.INFO, __file__, 1, 'got %s', ('where is my school',), None)
        flt.filter(rec)
        assert rec.getMessage() == 'got [redacted]'
    finally:
        privacy.clear_question(token)


# -- limits ------------------------------------------------------------------

def test_daily_budget_returns_friendly_message(app_client, claude, monkeypatch):
    import config

    monkeypatch.setattr(config, 'ASK_DAILY_BUDGET', 2)
    client = claude([search_ab(), final(f'[[{AB}]]', [AB])] * 2)
    assert ask(app_client).status_code == 200
    assert ask(app_client).status_code == 200
    calls_before = len(client.calls)
    res = ask(app_client)
    assert res.status_code == 429
    body = res.get_json()
    assert body['budget_exhausted'] is True
    assert 'try again tomorrow' in body['error']
    assert len(client.calls) == calls_before  # Claude never called


def test_daily_budget_shared_counter(env):
    from services import budget

    path = str(env.tmp / 'b.sqlite')
    assert budget.consume_daily_budget(limit=1, path=path, today='2026-10-17')
    assert not budget.consume_daily_budget(limit=1, path=path, today='2026-10-17')
    assert budget.consume_daily_budget(limit=1, path=path, today='2026-10-18')


def test_rate_limit_per_ip(app_client, claude, monkeypatch):
    import config

    monkeypatch.setattr(config, 'ASK_RATE_LIMIT_PER_MINUTE', 1)
    claude([search_ab(), final(f'[[{AB}]]', [AB])])
    assert ask(app_client).status_code == 200
    assert ask(app_client).status_code == 429


def test_tool_call_limit(app_client, claude, monkeypatch):
    import config

    monkeypatch.setattr(config, 'ASK_MAX_TOOL_CALLS', 2)
    many = tool_turn(*[tool_use('search_records', {'query': f'q{i}'}, id=f't{i}') for i in range(3)])
    client = claude([many, final('Edictai has no record of that.', [])])
    ask(app_client)
    results = client.calls[1]['messages'][-1]['content']
    assert results[2]['is_error'] is True
    assert client.calls[1]['tool_choice'] == {'type': 'none'}


def test_missing_question(app_client, env):
    assert ask(app_client, '').status_code == 400


# -- streaming ---------------------------------------------------------------

def test_stream_sends_progress_then_answer(app_client, claude):
    claude([
        tool_turn(tool_use('get_record', {'id': AB})),
        final(f'AB 1159 [[{AB}]].', [AB]),
    ])
    res = app_client.post('/api/ask/stream', json={'question': QUESTION})
    events = [json.loads(line[5:]) for line in res.get_data(as_text=True).split('\n') if line.startswith('data:')]
    assert events[0] == {'progress': 'Opening AB 1159…', 'tool': 'get_record'}
    kinds = [next(iter(e)) for e in events]
    assert kinds.index('answer') > kinds.index('progress')
    assert kinds[-1] == 'done'
    answer = next(e['answer'] for e in events if 'answer' in e)
    assert answer['records_layer']['citations'] == [AB]
    assert any('text' in e and 'AB 1159' in e['text'] for e in events)
