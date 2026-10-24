import os
from dotenv import load_dotenv

load_dotenv()

API_DIR = os.path.dirname(os.path.abspath(__file__))


def _int_env(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# Built from data/ by `edictai build`; the API only ever reads it.
DATABASE_PATH = os.getenv('EDICTAI_DB') or os.path.join(API_DIR, '..', 'build', 'edictai.db')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')

# --- Ask -------------------------------------------------------------------

# Model for the tool-use loop. Effort trades answer depth against latency.
ASK_MODEL = os.getenv('ASK_MODEL', 'claude-sonnet-5-5')
ASK_EFFORT = os.getenv('ASK_EFFORT', 'medium')
# 'between_tools' (thinking only between tool calls; fastest), 'adaptive', or
# 'default' to send no thinking setting. between_tools needs effort high or lower.
ASK_THINKING = os.getenv('ASK_THINKING', 'between_tools')
# 'default' lets the API rerun a declined request on a substitute model;
# 'off' disables that (required when pointing ASK_MODEL at a model without it).
ASK_FALLBACKS = os.getenv('ASK_FALLBACKS', 'default')
ASK_MAX_TOKENS = _int_env('ASK_MAX_TOKENS', 8000)
# Tool calls Claude may make while answering one question.
ASK_MAX_TOOL_CALLS = _int_env('ASK_MAX_TOOL_CALLS', 6)
# Records from a local search of the question placed in the first prompt, so
# Claude can often answer without a search round trip. 0 turns this off.
ASK_PRESEARCH_LIMIT = _int_env('ASK_PRESEARCH_LIMIT', 8)
# Small talk and off-topic messages go to this quicker model, without tools.
# Anything it judges to be a policy question goes on to ASK_MODEL. 'off' disables it.
ASK_FAST_MODEL = os.getenv('ASK_FAST_MODEL', 'claude-haiku-5-5')

# Per-IP limit, per worker process.
ASK_RATE_LIMIT_PER_MINUTE = _int_env('ASK_RATE_LIMIT_PER_MINUTE', 10)
# Questions answered per UTC day across all workers on the host; 0 disables the cap.
ASK_DAILY_BUDGET = _int_env('ASK_DAILY_BUDGET', 500)
ASK_BUDGET_FILE = os.getenv('ASK_BUDGET_FILE') or os.path.join(API_DIR, '.ask-budget.sqlite')

# Policies Claude mentions from general knowledge that the dataset lacks.
# Each line holds only the claimed item; never the question or anything about the asker.
ASK_LEADS_FILE = os.getenv('ASK_LEADS_FILE') or os.path.join(API_DIR, 'leads.jsonl')

# Where a visitor can report a policy the dataset is missing.
ASK_SUGGEST_URL = os.getenv(
    'ASK_SUGGEST_URL', 'https://github.com/asiagenawi/edictai/issues/new'
)
