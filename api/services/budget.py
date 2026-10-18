"""Request limits for Ask: per-IP rate limit and a global daily budget.

The per-IP window lives in this process's memory and is never written out.
The daily budget is a single counter per UTC day in a small SQLite file, so
every worker on the host draws from the same allowance.
"""

import datetime
import sqlite3
import threading
import time

import config

_rate = {}
_rate_lock = threading.Lock()
WINDOW_SECONDS = 60


def check_rate_limit(ip, limit=None, now=None):
    limit = config.ASK_RATE_LIMIT_PER_MINUTE if limit is None else limit
    now = time.time() if now is None else now
    with _rate_lock:
        if len(_rate) > 10_000:
            for key in [k for k, ts in _rate.items() if not ts or now - ts[-1] >= WINDOW_SECONDS]:
                del _rate[key]
        stamps = [t for t in _rate.get(ip, []) if now - t < WINDOW_SECONDS]
        if len(stamps) >= limit:
            _rate[ip] = stamps
            return False
        stamps.append(now)
        _rate[ip] = stamps
        return True


def reset_rate_limits():
    with _rate_lock:
        _rate.clear()


def consume_daily_budget(limit=None, path=None, today=None):
    """Count one question against today's allowance; False once it is spent."""
    limit = config.ASK_DAILY_BUDGET if limit is None else limit
    if limit <= 0:
        return True
    path = path or config.ASK_BUDGET_FILE
    today = today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    conn = sqlite3.connect(path, timeout=5, isolation_level=None)
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS daily (day TEXT PRIMARY KEY, used INTEGER NOT NULL)')
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT used FROM daily WHERE day = ?', (today,)).fetchone()
        used = row[0] if row else 0
        if used >= limit:
            conn.execute('COMMIT')
            return False
        conn.execute('INSERT INTO daily (day, used) VALUES (?, 1) '
                     'ON CONFLICT (day) DO UPDATE SET used = used + 1', (today,))
        conn.execute('DELETE FROM daily WHERE day < ?', (today,))
        conn.execute('COMMIT')
        return True
    finally:
        conn.close()
