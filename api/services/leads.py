"""Anonymous leads: policies Claude named from general knowledge that the dataset lacks.

A lead holds only the claimed item (jurisdiction, identifier, title, a one-line
description) and the day it was seen. It never holds the question, the
conversation, an IP address, or anything else about the person asking; a lead
that repeats the question's wording is dropped rather than risk carrying it.
"""

import datetime
import fcntl
import json
import logging
import os
import re

import config

logger = logging.getLogger(__name__)

_LIMITS = {'jurisdiction': 40, 'identifier': 40, 'title': 200, 'claim': 280}
_EMAIL_RE = re.compile(r'\S+@\S+')
_URL_RE = re.compile(r'https?://\S+')


def _clean(value, limit):
    if not isinstance(value, str):
        return None
    value = _URL_RE.sub('', _EMAIL_RE.sub('', value))
    value = ' '.join(value.split())
    return value[:limit] or None


def _norm(text):
    return ' '.join(re.findall(r'[a-z0-9]+', (text or '').lower()))


def _echoes_question(fields, question):
    q = _norm(question)
    if len(q) < 8:
        return False
    return any(q in _norm(v) for v in fields.values() if v)


def build_leads(items, question, idx):
    """Leads for the claimed policies the dataset does not already hold."""
    leads = []
    seen = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        fields = {k: _clean(item.get(k), n) for k, n in _LIMITS.items()}
        if not (fields['identifier'] or fields['title']):
            continue
        if _echoes_question(fields, question):
            continue
        jid = idx.resolve_jurisdiction(fields['jurisdiction'])
        if jid:
            fields['jurisdiction'] = jid
        if idx.find_policy(jid, fields['identifier'], fields['title']):
            continue
        key = (fields['jurisdiction'], fields['identifier'], fields['title'])
        if key in seen:
            continue
        seen.add(key)
        leads.append({
            **{k: v for k, v in fields.items() if v},
            'seen': datetime.date.today().isoformat(),
            'source': 'ask-general-knowledge',
        })
    return leads


def record_leads(items, question, idx):
    leads = build_leads(items, question, idx)
    if not leads:
        return []
    path = config.ASK_LEADS_FILE
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        data = ''.join(json.dumps(lead, ensure_ascii=False) + '\n' for lead in leads)
        with open(path, 'a', encoding='utf-8') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(data)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except OSError as e:
        logger.warning('could not write leads: %s', type(e).__name__)
    return leads
