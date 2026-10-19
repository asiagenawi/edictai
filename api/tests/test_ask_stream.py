"""Streaming previews, the pre-search, and the quick small-talk path."""

import json
from types import SimpleNamespace

import pytest

from conftest import final, tool_turn, tool_use

AB = 'us-ca-2025-2026-ab-1159'
SB = 'us-ca-2023-2024-sb-1288'
DOC = 'us-ca-cde-ai-guidance'
QUESTION = 'Which California laws protect pupil data from AI vendors?'


def events(app_client, question=QUESTION, **extra):
    res = app_client.post('/api/ask/stream', json={'question': question, **extra})
    return [json.loads(line[5:]) for line in res.get_data(as_text=True).split('\n') if line.startswith('data:')]


def streamed(evs, key='delta'):
    return ''.join(e[key] for e in evs if key in e)


def fast(kind, text):
    return SimpleNamespace(content=[SimpleNamespace(type='text', text=json.dumps({'kind': kind, 'text': text}))],
                           stop_reason='end_turn')


def search_ab():
    return tool_turn(tool_use('search_records', {'query': 'pupil data', 'jurisdiction': 'CA'}))


@pytest.fixture
def presearch_on(monkeypatch):
    import config
    monkeypatch.setattr(config, 'ASK_PRESEARCH_LIMIT', 8)


@pytest.fixture
def fast_on(monkeypatch):
    import config
    monkeypatch.setattr(config, 'ASK_FAST_MODEL', 'claude-haiku-5-5')


# -- the partial JSON scanner ------------------------------------------------

def test_scan_json_prefix_reads_unfinished_strings():
    from services.ask_stream import scan_json_prefix

    buf = '{"kind": "records", "records_layer": {"text": "AB 1159 \\"limits\\" pupil\\n da'
    found = scan_json_prefix(buf)
    assert found[('kind',)] == ('records', True)
    assert found[('records_layer', 'text')] == ('AB 1159 "limits" pupil\n da', False)

    full = json.dumps({'kind': 'records', 'records_layer': {'text': 'Café \U0001F600 ok', 'citations': ['a', 'b']},
                       'table': None, 'general_knowledge': {'text': 'GK', 'mentioned_policies': []}})
    found = scan_json_prefix(full)
    assert found[('records_layer', 'text')] == ('Café \U0001F600 ok', True)
    assert found[('records_layer', 'citations', 1)] == ('b', True)
    assert found[('general_knowledge', 'text')] == ('GK', True)
    # A unicode escape cut in half is held back, never garbled.
    assert scan_json_prefix('{"kind": "x", "t": "a\\u00')[('t',)] == ('a', False)
    assert scan_json_prefix('{"kind": "x", "t": "a\\ud83d\\ude')[('t',)] == ('a', False)


def test_every_prefix_of_an_answer_scans_without_error():
    from services.ask_stream import scan_json_prefix

    full = json.dumps({'kind': 'records', 'records_layer': {'text': 'Line "one"\n- two — [[x]]', 'citations': ['x']},
                       'table': ['x'], 'general_knowledge': None, 'follow_ups': ['More?']})
    last = ''
    for i in range(len(full) + 1):
        value = scan_json_prefix(full[:i]).get(('records_layer', 'text'), ('', False))[0]
        assert value.startswith(last) or not last
        last = value or last
    assert last == 'Line "one"\n- two — [[x]]'


# -- streaming deltas --------------------------------------------------------

def test_stream_sends_text_deltas_before_the_checked_answer(app_client, claude):
    text = f'California enacted AB 1159 [[{AB}]], which limits how AI vendors use pupil data.'
    claude([search_ab(), final(text, [AB], follow_ups=['When does it take effect?'])])
    evs = events(app_client)
    kinds = [next(iter(e)) for e in evs]
    assert kinds.count('delta') > 3
    assert kinds.index('delta') < kinds.index('answer')
    answer = next(e['answer'] for e in evs if 'answer' in e)
    # The preview grows into exactly the final records text.
    assert streamed(evs) == answer['records_layer']['text']
    # The chip for a cited record arrives with the delta that completes its marker.
    with_records = [e for e in evs if 'records' in e and 'delta' in e]
    assert len(with_records) == 1 and AB in with_records[0]['records']
    assert with_records[0]['records'][AB]['identifier'] == 'AB 1159'
    # Older frames are still sent.
    assert 'sources' in kinds and 'text' in kinds and kinds[-1] == 'done'


def test_preview_shows_whole_words_and_no_half_markers(app_client, claude):
    text = f'California enacted AB 1159 [[{AB}]] in 2026.'
    claude([search_ab(), final(text, [AB])])
    evs = events(app_client)
    sofar = ''
    for e in evs:
        if 'delta' in e:
            sofar += e['delta']
            assert sofar.count('[[') == sofar.count(']]')
            assert not sofar.endswith(' ')


def test_preview_stops_before_an_unretrieved_citation_and_is_retracted(app_client, claude):
    bad = f'AB 1159 [[{AB}]] limits pupil data, as does [[{SB}]] for sure.'
    good = f'AB 1159 [[{AB}]] limits pupil data.'
    claude([search_ab(), final(bad, [AB, SB]), final(good, [AB])])
    evs = events(app_client)
    kinds = [next(iter(e)) for e in evs]
    first_attempt = ''.join(e['delta'] for e in evs[:kinds.index('retract')] if 'delta' in e)
    assert SB not in first_attempt and first_attempt.startswith('AB 1159')
    assert kinds.index('retract') < kinds.index('progress', kinds.index('retract'))
    after = evs[kinds.index('retract'):]
    assert ''.join(e.get('delta', '') for e in after) == good
    answer = next(e['answer'] for e in evs if 'answer' in e)
    assert answer['records_layer']['text'] == good


def test_preview_never_shows_unsupported_bill_numbers(app_client, claude):
    text = f'AB 1159 [[{AB}]] and also SB 777 restrict vendors.'
    claude([search_ab(), final(text, [AB]), final(text, [AB])])
    evs = events(app_client)
    assert 'SB 777' not in streamed(evs)
    answer = next(e['answer'] for e in evs if 'answer' in e)
    assert 'SB 777' not in answer['records_layer']['text']


def test_general_knowledge_streams_tagged_and_matches_final(app_client, claude):
    claude([search_ab(), final(f'California enacted AB 1159 [[{AB}]].', [AB], general_knowledge={
        'text': 'Texas HB 2 may address chatbots in schools.', 'mentioned_policies': []})])
    evs = events(app_client)
    answer = next(e['answer'] for e in evs if 'answer' in e)
    assert streamed(evs, 'gk_delta') == answer['general_knowledge']['text']
    assert '(unverified)' in streamed(evs, 'gk_delta')


def test_tool_turn_text_is_retracted(app_client, claude):
    # Text written before a tool call is not the answer.
    pre = json.dumps({'kind': 'records', 'records_layer': {'text': 'Let me look that up for you now.', 'citations': []}})
    turn = SimpleNamespace(content=[SimpleNamespace(type='text', text=pre),
                                    tool_use('search_records', {'query': 'pupil data'})], stop_reason='tool_use')
    claude([search_ab(), turn, final(f'AB 1159 [[{AB}]].', [AB])])
    evs = events(app_client)
    kinds = [next(iter(e)) for e in evs]
    assert 'retract' in kinds
    assert ''.join(e.get('delta', '') for e in evs[kinds.index('retract'):]) == f'AB 1159 [[{AB}]].'


def test_conversation_reply_streams(app_client, claude):
    claude([final('Hi! What are you looking into today?', kind='conversation')])
    evs = events(app_client, 'hi')
    assert streamed(evs) == 'Hi! What are you looking into today?'


# -- the pre-search ----------------------------------------------------------

def test_presearch_puts_matches_in_the_first_prompt(app_client, claude, presearch_on):
    text = f'AB 1159 [[{AB}]] limits how AI tools use pupil data.'
    client = claude([final(text, [AB])])
    evs = events(app_client, 'What does California AB 1159 do?')
    # Answered from the pre-search alone: one model call, no correction round.
    assert len(client.calls) == 1
    first = client.calls[0]
    user = first['messages'][-1]['content']
    assert 'Search results for this message' in user and AB in user
    # The bill the question names comes in full, so no get_record round trip is needed.
    assert 'In full' in user and 'Bars use of pupil data to train AI models.' in user
    assert user.endswith('Message: What does California AB 1159 do?')
    # The cached prefix (tools, then the system prompt) carries none of it.
    assert first['system'][0]['cache_control'] == {'type': 'ephemeral'}
    from services.ask_agent import SYSTEM_PROMPT
    assert first['system'][0]['text'] == SYSTEM_PROMPT
    assert first['cache_control'] == {'type': 'ephemeral'}
    answer = next(e['answer'] for e in evs if 'answer' in e)
    assert answer['records_layer']['citations'] == [AB]
    assert streamed(evs) == text


def test_presearch_order_ranks_the_named_bill_first(env, presearch_on):
    from services import ask_agent

    lines, ids, detail = ask_agent.presearch('What does California AB 1159 do?')
    assert ids[0] == AB
    assert detail == [AB]
    assert any("jurisdiction='us-ca'" in line for line in lines)


def test_presearch_follow_up_includes_the_records_just_cited(env, presearch_on):
    from services import ask_agent

    history = [{'role': 'user', 'content': 'What does AB 1159 do?'},
               {'role': 'assistant', 'content': f'It limits pupil data use [[{AB}]].'}]
    lines, ids, detail = ask_agent.presearch('When does it take effect?', history)
    assert ids[0] == AB
    assert detail == [AB]


def test_presearch_uses_page_scope(env, presearch_on):
    from services import ask_agent

    _, ids, _ = ask_agent.presearch('What guidance is there for teachers?', scope={'jurisdiction': 'us-ca'})
    assert DOC in ids


def test_presearch_ids_still_must_be_cited_correctly(app_client, claude, presearch_on):
    bad = 'us-tx-2025-hb-9999'
    client = claude([final(f'AB 1159 [[{AB}]] and [[{bad}]].', [AB, bad]),
                     final(f'AB 1159 [[{AB}]].', [AB])])
    body = app_client.post('/api/ask', json={'question': 'What does California AB 1159 do?'}).get_json()
    assert len(client.calls) == 2
    assert bad in client.calls[1]['messages'][-1]['content']
    assert body['records_layer']['citations'] == [AB]


def test_without_presearch_matches_tools_are_still_required(app_client, claude, presearch_on):
    client = claude([final("I couldn't find that.", []), tool_turn(tool_use('search_records', {'query': 'zzz'})),
                     final("I couldn't find that.", [])])
    app_client.post('/api/ask', json={'question': 'zzqx frobnicate?'})
    assert 'No tools were consulted' in client.calls[1]['messages'][-1]['content']


# -- the quick small-talk path -----------------------------------------------

def test_small_talk_goes_to_the_fast_model_without_tools(app_client, claude, fast_on):
    client = claude([fast('conversation', 'Hi! What are you looking into today?')])
    evs = events(app_client, 'hi')
    assert len(client.calls) == 1
    kw = client.calls[0]
    assert kw['model'] == 'claude-haiku-5-5'
    assert 'tools' not in kw
    assert kw['output_config']['effort'] == 'low'
    assert kw['thinking'] == {'type': 'disabled'}
    answer = next(e['answer'] for e in evs if 'answer' in e)
    assert answer['kind'] == 'conversation' and answer['model'] == 'claude-haiku-5-5'
    assert answer['records_layer'] == {'text': 'Hi! What are you looking into today?', 'citations': []}
    assert streamed(evs) == 'Hi! What are you looking into today?'


def test_off_topic_goes_to_the_fast_model(app_client, claude, fast_on):
    reply = "I'm not knowledgeable about the weather, but ask me which states have AI laws for schools."
    client = claude([fast('conversation', reply)])
    body = app_client.post('/api/ask', json={'question': 'whats the weather'}).get_json()
    assert client.calls[0]['model'] == 'claude-haiku-5-5'
    assert body['records_layer']['text'] == reply


def test_fast_model_hands_policy_messages_on(app_client, claude, fast_on):
    client = claude([fast('records', ''), search_ab(), final(f'AB 1159 [[{AB}]].', [AB])])
    body = app_client.post('/api/ask', json={'question': 'what about that one'}).get_json()
    assert [c['model'] for c in client.calls] == ['claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-sonnet-5-5']
    assert client.calls[1]['tools']
    assert body['records_layer']['citations'] == [AB]
    assert body['model'] == 'claude-sonnet-5-5'


def test_fast_reply_naming_a_bill_is_handed_on(app_client, claude, fast_on):
    client = claude([fast('conversation', 'Sure, SB 777 is a good one.'),
                     final('Happy to help. What are you looking into?', kind='conversation')])
    evs = events(app_client, 'thanks')
    assert [c['model'] for c in client.calls] == ['claude-haiku-5-5', 'claude-sonnet-5-5']
    assert 'SB 777' not in streamed(evs)
    answer = next(e['answer'] for e in evs if 'answer' in e)
    assert answer['records_layer']['text'] == 'Happy to help. What are you looking into?'


def test_fast_model_failure_falls_back_to_main(app_client, claude, fast_on):
    import anthropic

    def boom(kwargs):
        raise anthropic.APIConnectionError(request=None)

    client = claude([boom, final('Hi there!', kind='conversation')])
    body = app_client.post('/api/ask', json={'question': 'hello'}).get_json()
    assert len(client.calls) == 2
    assert body['records_layer']['text'] == 'Hi there!'


@pytest.mark.parametrize('question', [
    'What does California AB 1159 do?',
    'Which states require districts to adopt an AI policy?',
    'Has Wyoming passed a law on AI in schools?',
    'what about Texas?',
    'deepfakes?',
    'any new bills',
    'is chatgpt banned',
    'What does HB 1481 say',
])
def test_policy_questions_never_take_the_fast_path(env, fast_on, question):
    from services import ask_agent
    assert not ask_agent.use_fast_path(question)


@pytest.mark.parametrize('question', ['hi', 'thanks!', 'whats the weather', 'i just ate', 'how are you'])
def test_small_talk_takes_the_fast_path(env, fast_on, question):
    from services import ask_agent
    assert ask_agent.use_fast_path(question)


def test_yes_after_an_offer_stays_on_the_main_path(env, fast_on):
    from services import ask_agent
    history = [{'role': 'assistant', 'content': 'Want the deadlines?'}]
    assert not ask_agent.use_fast_path('yes please', history)


def test_fast_path_can_be_turned_off(env, monkeypatch):
    import config
    from services import ask_agent
    monkeypatch.setattr(config, 'ASK_FAST_MODEL', 'off')
    assert not ask_agent.use_fast_path('hi')


def test_response_blocks_are_sent_back_as_plain_params():
    from services.ask_agent import _content_params

    class Block:
        def model_dump(self, exclude_none=False):
            return {'type': 'text', 'text': '{}', 'parsed_output': None, 'citations': None} if not exclude_none \
                else {'type': 'text', 'text': '{}'}

    assert _content_params([Block()]) == [{'type': 'text', 'text': '{}'}]


def test_which_states_questions_skip_the_presearch(env, presearch_on):
    from services import ask_agent

    assert ask_agent.presearch('Which states require districts to adopt an AI policy?') == ([], [], [])
    assert ask_agent.presearch('How many states have AI task forces?') == ([], [], [])


def test_uncited_reply_from_presearch_alone_is_sent_back(app_client, claude, presearch_on):
    # The search results didn't answer it, and the model wrote a plan instead of calling a tool.
    client = claude([final("I'll check Edictai's records for California guidance.", []),
                     search_ab(), final(f'AB 1159 [[{AB}]].', [AB])])
    body = app_client.post('/api/ask', json={'question': 'What does California say about pupil data?'}).get_json()
    assert 'cites nothing and no tools were used' in client.calls[1]['messages'][-1]['content']
    assert body['records_layer']['citations'] == [AB]
