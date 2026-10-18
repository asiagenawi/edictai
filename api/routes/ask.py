"""Ask endpoints.

POST /api/ask          -> one JSON response.
POST /api/ask/stream   -> server-sent events: `progress` lines while tools run,
                          then the structured `answer`, then `done`.

Request body: {"question": str, "history"?: [{role, content}], "scope"?: {jurisdiction, level, record_id}}.
Both responses keep the earlier fields (`answer` as Markdown, `sources`) beside
the structured answer, so older clients keep working.

Questions and answers are not logged or stored.
"""

import json
import logging

from flask import Blueprint, Response, request

from services import budget, privacy
from services.ask_agent import AskUnavailable, legacy_sources, render_markdown, run_ask

ask_bp = Blueprint('ask', __name__)

logger = logging.getLogger(__name__)

GENERIC_ERROR = 'Sorry, the answer service is unavailable right now. Please try again.'
RATE_LIMITED = 'Too many questions in a short time. Please wait a minute and try again.'
BUDGET_SPENT = (
    "Ask Edictai has answered as many questions as it can today. Please try again tomorrow; "
    'the map and every record remain available in the meantime.'
)
MAX_QUESTION_CHARS = 2000


def _read_request():
    """Returns ((question, history, scope), None) or (None, error_response)."""
    if not budget.check_rate_limit(request.remote_addr or 'unknown'):
        return None, ({'error': RATE_LIMITED}, 429)

    data = request.get_json(silent=True) or {}
    question = data.get('question')
    question = question.strip() if isinstance(question, str) else ''
    if not question:
        return None, ({'error': 'Question is required'}, 400)
    if len(question) > MAX_QUESTION_CHARS:
        return None, ({'error': f'Please keep questions under {MAX_QUESTION_CHARS} characters.'}, 400)

    history = data.get('history') if isinstance(data.get('history'), list) else None
    scope = data.get('scope') if isinstance(data.get('scope'), dict) else None

    if not budget.consume_daily_budget():
        return None, ({'error': BUDGET_SPENT, 'budget_exhausted': True}, 429)
    return (question, history, scope), None


def _legacy_fields(payload):
    return {'answer': render_markdown(payload), 'sources': legacy_sources(payload)}


@ask_bp.route('/api/ask', methods=['POST'])
def ask():
    parsed, error = _read_request()
    if error:
        return error
    question, history, scope = parsed
    token = privacy.set_question(question)
    try:
        payload = None
        for kind, data in run_ask(question, history, scope):
            if kind == 'answer':
                payload = data
        return {'question': question, **payload, **_legacy_fields(payload)}
    except AskUnavailable:
        return {'error': GENERIC_ERROR}, 503
    except Exception as e:
        logger.error('ask failed: %s', type(e).__name__)
        return {'error': GENERIC_ERROR}, 500
    finally:
        privacy.clear_question(token)


def _sse(obj):
    return f'data: {json.dumps(obj, ensure_ascii=False)}\n\n'


@ask_bp.route('/api/ask/stream', methods=['POST'])
def ask_stream():
    parsed, error = _read_request()
    if error:
        return error
    question, history, scope = parsed

    def generate():
        token = privacy.set_question(question)
        try:
            for kind, data in run_ask(question, history, scope):
                if kind == 'progress':
                    yield _sse({'progress': data['message'], 'tool': data.get('tool')})
                else:
                    legacy = _legacy_fields(data)
                    yield _sse({'answer': data})
                    yield _sse({'sources': legacy['sources']})
                    # A single `text` frame carries the whole Markdown answer for
                    # clients that only read text deltas.
                    yield _sse({'text': legacy['answer']})
        except AskUnavailable:
            yield _sse({'error': GENERIC_ERROR})
        except Exception as e:
            logger.error('ask stream failed: %s', type(e).__name__)
            yield _sse({'error': GENERIC_ERROR})
        finally:
            privacy.clear_question(token)
        yield _sse({'done': True})

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',  # don't let a proxy buffer the stream
        },
    )
