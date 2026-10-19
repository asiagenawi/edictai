"""Tools Claude uses to answer from the dataset.

Every tool is a fixed, parameterised read over the built database or the
in-memory search index; none accepts SQL. Each call records the ids it
returned in the turn's `TurnContext`, which is what answer validation checks
citations against.
"""

import json
import re

from models.database import get_db
from services.search_index import LEVELS, RECORD_TYPES, get_index, normalize_level, plain_verification

MAX_RESULTS = 15
DATE_RE = re.compile(r'^\d{4}(-\d{2}(-\d{2})?)?$')


class ToolInputError(ValueError):
    """Bad tool arguments; reported back to Claude so it can retry."""


class TurnContext:
    """What the tools returned while answering one question."""

    def __init__(self):
        self.returned_ids = set()
        self.primary_ids = []  # records surfaced as results, in order, for fallbacks
        self.searched = []  # human-readable descriptions of each lookup
        self.tool_calls = 0

    def register(self, ids, primary=True):
        for rid in ids:
            if not rid:
                continue
            self.returned_ids.add(rid)
            if primary and rid not in self.primary_ids:
                self.primary_ids.append(rid)


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------

def _vocab():
    db = get_db()
    try:
        topics = [r[0] for r in db.execute('SELECT code FROM topics ORDER BY position')]
        ptypes = [r[0] for r in db.execute('SELECT code FROM provision_types ORDER BY position')]
    finally:
        db.close()
    return topics, ptypes


_DEFINITIONS = None


def tool_definitions():
    """Tool schemas, built once from the vocabularies so the list is byte-stable for caching."""
    global _DEFINITIONS
    if _DEFINITIONS is not None:
        return _DEFINITIONS
    topics, ptypes = _vocab()
    jur = {'type': 'string', 'description': "State or federal: 'TX', 'Texas', 'us-tx', or 'us' for federal."}
    level = {'type': 'string', 'enum': list(LEVELS), 'description': 'Education level.'}
    date = {'type': 'string', 'description': 'YYYY, YYYY-MM, or YYYY-MM-DD.'}
    topic = {'type': 'string', 'enum': topics}

    def obj(props, required=()):
        return {'type': 'object', 'properties': props, 'required': list(required),
                'additionalProperties': False}

    _DEFINITIONS = [
        {
            'name': 'search_records',
            'description': (
                'Full-text search (BM25) over legislation, policy documents, executive actions, '
                'and research: titles, identifiers, summaries, cited provisions, and legislative '
                'action text. Filters are exact. Returns ids with one-line summaries; call '
                'get_record for detail. Omit query to list by filters alone, most recent first.'
            ),
            'input_schema': obj({
                'query': {'type': 'string', 'description': 'Keywords or a bill number, e.g. "deepfake" or "HB 1481".'},
                'jurisdiction': jur,
                'level': level,
                'topic': topic,
                'record_type': {'type': 'string', 'enum': list(RECORD_TYPES)},
                'status': {
                    'type': 'string',
                    'description': (
                        "Exact status. Legislation: introduced, in-committee, passed-chamber, "
                        "passed-legislature, enacted, vetoed, failed. Documents: current, "
                        "superseded, withdrawn. Executive actions: active, rescinded, expired, superseded."
                    ),
                },
                'in_force': {'type': 'boolean', 'description': 'Only enacted laws, current documents, active orders.'},
                'since': date,
                'until': date,
                'limit': {'type': 'integer', 'description': f'Max results (default 10, max {MAX_RESULTS}).'},
            }),
        },
        {
            'name': 'get_record',
            'description': (
                'Full record by id: summary, actions and derived status, cited provisions, '
                'versions, sponsors, sources, verification, and related records.'
            ),
            'input_schema': obj({'id': {'type': 'string'}}, ['id']),
        },
        {
            'name': 'provision_grid',
            'description': (
                'Which jurisdictions have records containing a given kind of provision, '
                'with the record ids. Use for "which states require ..." questions. Without '
                'provision_type, returns counts per provision type.'
            ),
            'input_schema': obj({
                'provision_type': {'type': 'string', 'enum': ptypes},
                'jurisdictions': {'type': 'array', 'items': {'type': 'string'}},
                'level': level,
                'in_force': {'type': 'boolean', 'description': 'Only records currently in force.'},
            }),
        },
        {
            'name': 'state_overview',
            'description': (
                'One jurisdiction at a glance: record counts by type and status, laws in force, '
                'current guidance and policy documents, and the most recent changes.'
            ),
            'input_schema': obj({'jurisdiction': jur, 'level': level}, ['jurisdiction']),
        },
        {
            'name': 'recent_changes',
            'description': (
                'Dated changes on or after a date: legislative actions, documents published or '
                'updated, executive actions signed or ended. Most recent first.'
            ),
            'input_schema': obj({'since': date, 'jurisdiction': jur, 'level': level}, ['since']),
        },
        {
            'name': 'search_research',
            'description': 'Search the research library (studies, reports, surveys) with key findings and their citations.',
            'input_schema': obj({'query': {'type': 'string'}, 'topic': topic, 'level': level}, ['query']),
        },
        {
            'name': 'stats',
            'description': (
                'Fixed aggregate counts. measure picks what is counted; group_by picks the '
                'breakdown. Each group lists up to 20 record ids.'
            ),
            'input_schema': obj({
                'measure': {'type': 'string', 'enum': list(MEASURES)},
                'group_by': {'type': 'string', 'enum': list(GROUP_BY)},
                'jurisdiction': jur,
                'level': level,
                'topic': topic,
                'record_type': {'type': 'string', 'enum': list(RECORD_TYPES)},
                'since': date,
                'until': date,
            }, ['measure', 'group_by']),
        },
    ]
    return _DEFINITIONS


# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------

def _jurisdiction(value, required=False):
    if not value:
        if required:
            raise ToolInputError('jurisdiction is required')
        return None
    jid = get_index().resolve_jurisdiction(value)
    if not jid:
        raise ToolInputError(f'unknown jurisdiction {value!r}; use a state name, USPS code, or "us"')
    return jid


def _level(value):
    if not value:
        return None
    lvl = normalize_level(value)
    if not lvl:
        raise ToolInputError("level must be 'k12' or 'postsecondary'")
    return lvl


def _date(value, name):
    if not value:
        return None
    value = str(value).strip()
    if not DATE_RE.match(value):
        raise ToolInputError(f'{name} must be YYYY, YYYY-MM, or YYYY-MM-DD')
    return value


def _limit(value, default=10):
    try:
        return max(1, min(MAX_RESULTS, int(value)))
    except (TypeError, ValueError):
        return default


def _for_model(brief):
    """A brief without the fields only the interface needs."""
    return {k: v for k, v in brief.items() if k not in ('source_url', 'num_id') and v is not None}


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------

def search_records(ctx, args):
    idx = get_index()
    record_type = args.get('record_type')
    if record_type and record_type not in RECORD_TYPES:
        raise ToolInputError(f'record_type must be one of {", ".join(RECORD_TYPES)}')
    results, total = idx.search(
        query=args.get('query'),
        jurisdiction=_jurisdiction(args.get('jurisdiction')),
        level=_level(args.get('level')),
        topic=args.get('topic') or None,
        record_types=[record_type] if record_type else None,
        status=args.get('status') or None,
        current_only=bool(args.get('in_force')),
        since=_date(args.get('since'), 'since'),
        until=_date(args.get('until'), 'until'),
        limit=_limit(args.get('limit')),
    )
    ctx.register(r['id'] for r in results)
    return {'total_matches': total, 'results': [_for_model(r) for r in results]}


def get_record(ctx, args):
    rid = (args.get('id') or '').strip()
    idx = get_index()
    brief = idx.brief(rid)
    if not brief:
        return {'error': f'No record with id {rid!r}. Record ids come from search results.'}
    db = get_db()
    try:
        out = dict(_for_model(brief))
        out['summary'] = _one(db, f'SELECT summary FROM {_table(brief["type"])} WHERE id = ?', rid)
        out['topics'] = _col(db, 'SELECT topic FROM record_topics WHERE record_id = ?', rid)
        out['education_levels'] = _col(db, 'SELECT education_level FROM record_education_levels WHERE record_id = ?', rid)
        out['verification'] = dict(db.execute(
            f'SELECT verification_status AS status, last_verified, verification_notes AS notes '
            f'FROM {_table(brief["type"])} WHERE id = ?', (rid,)).fetchone())
        out['verification']['status'] = plain_verification(out['verification']['status'])
        out['verification'] = {k: v for k, v in out['verification'].items() if v}

        if brief['type'] == 'legislation':
            leg = dict(db.execute(
                'SELECT session, abstract, session_law, status_date, introduced_date, enacted_date, '
                'last_action_date FROM legislation WHERE id = ?', (rid,)).fetchone())
            out.update({k: v for k, v in leg.items() if v})
            out['actions'] = [dict(r) for r in db.execute(
                'SELECT date, actor, description FROM legislation_actions '
                'WHERE legislation_id = ? ORDER BY seq', (rid,))]
            out['sponsors'] = [
                {k: v for k, v in dict(r).items() if v is not None}
                for r in db.execute(
                    'SELECT name, party, chamber, primary_sponsor AS "primary" FROM legislation_sponsors '
                    'WHERE legislation_id = ? ORDER BY seq', (rid,))
            ]
            out['versions'] = [dict(r) for r in db.execute(
                'SELECT note, date, url FROM legislation_versions WHERE legislation_id = ? ORDER BY seq', (rid,))]
        elif brief['type'] == 'document':
            doc = dict(db.execute(
                'SELECT d.published, d.updated, d.version, d.effective, d.superseded_by, '
                'd.authorized_by, o.name AS issuer FROM documents d '
                'LEFT JOIN organizations o ON o.id = d.issuer WHERE d.id = ?', (rid,)).fetchone())
            out.update({k: v for k, v in doc.items() if v})
        elif brief['type'] == 'executive-action':
            ea = dict(db.execute(
                'SELECT e.number, e.signed, e.ended, e.expires, o.name AS issuer FROM executive_actions e '
                'LEFT JOIN organizations o ON o.id = e.issuer WHERE e.id = ?', (rid,)).fetchone())
            out.update({k: v for k, v in ea.items() if v})
        elif brief['type'] == 'research':
            rs = dict(db.execute('SELECT publisher, published, methods FROM research WHERE id = ?', (rid,)).fetchone())
            out.update({k: v for k, v in rs.items() if v})
            out['authors'] = _col(db, 'SELECT name FROM research_authors WHERE research_id = ? ORDER BY seq', rid)
            out['key_findings'] = _findings(db, rid)

        out['provisions'] = [
            {k: v for k, v in dict(r).items() if v}
            for r in db.execute(
                'SELECT type, description, locator, quote, source_url FROM provisions '
                'WHERE record_id = ? ORDER BY seq', (rid,))
        ]
        out['sources'] = [
            {k: v for k, v in dict(r).items() if v}
            for r in db.execute(
                'SELECT title, publisher, url, kind, accessed FROM sources WHERE record_id = ? ORDER BY seq', (rid,))
        ]
        related = _col(db, 'SELECT related_id FROM related_records WHERE record_id = ?', rid)
    finally:
        db.close()

    rel_briefs = idx.briefs(related)
    out['related'] = [
        {k: rel_briefs[r][k] for k in ('id', 'type', 'identifier', 'title', 'status') if rel_briefs[r].get(k)}
        for r in related if r in rel_briefs
    ]
    ctx.register([rid])
    ctx.register(rel_briefs, primary=False)
    return out


def provision_grid(ctx, args):
    idx = get_index()
    level = _level(args.get('level'))
    in_force = bool(args.get('in_force'))
    jurs = [_jurisdiction(j) for j in (args.get('jurisdictions') or [])]
    ptype = args.get('provision_type') or None

    clauses, params = ['1'], []
    if ptype:
        clauses.append('pr.type = ?')
        params.append(ptype)
    if jurs:
        clauses.append(f'vp.jurisdiction IN ({",".join("?" * len(jurs))})')
        params += jurs
    if level:
        clauses.append('EXISTS (SELECT 1 FROM provision_education_levels pel '
                       'WHERE pel.provision_id = pr.id AND pel.education_level = ?)')
        params.append(level)
    if in_force:
        clauses.append('vp.is_current = 1')
    where = ' AND '.join(clauses)

    db = get_db()
    try:
        if not ptype:
            rows = db.execute(f'''
                SELECT pt.code AS provision_type, pt.name,
                       COUNT(DISTINCT vp.jurisdiction) AS jurisdictions,
                       COUNT(DISTINCT pr.record_id) AS records
                FROM provisions pr
                JOIN v_policies vp ON vp.id = pr.record_id
                JOIN provision_types pt ON pt.code = pr.type
                WHERE {where}
                GROUP BY pt.code ORDER BY pt.position
            ''', params).fetchall()
            return {'provision_types': [dict(r) for r in rows],
                    'note': 'Call again with provision_type for the jurisdictions and record ids.'}
        name = _one(db, 'SELECT name FROM provision_types WHERE code = ?', ptype)
        rows = db.execute(f'''
            SELECT vp.jurisdiction, pr.record_id
            FROM provisions pr JOIN v_policies vp ON vp.id = pr.record_id
            WHERE {where}
            GROUP BY vp.jurisdiction, pr.record_id
            ORDER BY vp.jurisdiction
        ''', params).fetchall()
    finally:
        db.close()

    by_jur = {}
    for r in rows:
        by_jur.setdefault(r['jurisdiction'], []).append(r['record_id'])
    briefs = idx.briefs([r['record_id'] for r in rows])
    grid = []
    for jid, ids in by_jur.items():
        grid.append({
            'jurisdiction': jid,
            'name': idx.jurisdiction_name(jid),
            'records': [
                {k: briefs[i].get(k) for k in ('id', 'type', 'identifier', 'title', 'status', 'verification')}
                for i in ids if i in briefs
            ],
        })
    ctx.register(briefs)
    return {
        'provision_type': ptype,
        'provision_type_name': name,
        'jurisdictions_with_provision': len(grid),
        'grid': grid,
        'note': 'Jurisdictions not listed have no record with this provision in the dataset.',
    }


def state_overview(ctx, args):
    idx = get_index()
    jid = _jurisdiction(args.get('jurisdiction'), required=True)
    level = _level(args.get('level'))
    all_records, _ = idx.search(jurisdiction=jid, level=level, limit=10_000)
    counts = {}
    for r in all_records:
        counts.setdefault(r['type'], {}).setdefault(r['status'] or 'n/a', 0)
        counts[r['type']][r['status'] or 'n/a'] += 1
    in_force_laws = [r for r in all_records if r['type'] == 'legislation' and r['status'] == 'enacted']
    current_docs = [r for r in all_records if r['type'] == 'document' and r['status'] == 'current']
    active_orders = [r for r in all_records if r['type'] == 'executive-action' and r['status'] == 'active']
    pending = [r for r in all_records if r['type'] == 'legislation'
               and r['status'] in ('introduced', 'in-committee', 'passed-chamber', 'passed-legislature')]
    recent = sorted(all_records, key=lambda r: r['last_activity'] or '', reverse=True)[:5]

    def short(rs):
        return [{k: r.get(k) for k in ('id', 'identifier', 'title', 'status', 'classification',
                                       'last_activity', 'verification') if r.get(k)} for r in rs]

    shown = in_force_laws + current_docs + active_orders + pending[:10] + recent
    ctx.register(r['id'] for r in shown)
    return {
        'jurisdiction': jid,
        'name': idx.jurisdiction_name(jid),
        'level': level,
        'counts': counts,
        'laws_enacted': short(in_force_laws),
        'current_documents': short(current_docs),
        'active_executive_actions': short(active_orders),
        'pending_legislation': short(pending[:10]),
        'most_recent_activity': short(recent),
    }


def recent_changes(ctx, args):
    idx = get_index()
    since = _date(args.get('since'), 'since')
    if not since:
        raise ToolInputError('since is required')
    jid = _jurisdiction(args.get('jurisdiction'))
    level = _level(args.get('level'))

    filt, params = '', []
    if jid:
        filt += ' AND x.jurisdiction = ?'
        params.append(jid)
    if level:
        filt += (' AND EXISTS (SELECT 1 FROM record_education_levels el '
                 'WHERE el.record_id = x.record_id AND el.education_level = ?)')
        params.append(level)

    db = get_db()
    try:
        rows = db.execute(f'''
            SELECT * FROM (
                SELECT a.date, a.legislation_id AS record_id, l.jurisdiction, a.description AS change
                FROM legislation_actions a JOIN legislation l ON l.id = a.legislation_id
                UNION ALL
                SELECT d.published, d.id, d.jurisdiction, 'Published' FROM documents d WHERE d.published IS NOT NULL
                UNION ALL
                SELECT d.updated, d.id, d.jurisdiction, 'Updated' FROM documents d
                WHERE d.updated IS NOT NULL AND d.updated <> COALESCE(d.published, '')
                UNION ALL
                SELECT r.observed, r.document_id, d.jurisdiction, COALESCE(r.note, 'New version observed')
                FROM document_revisions r JOIN documents d ON d.id = r.document_id WHERE r.seq > 0
                UNION ALL
                SELECT e.signed, e.id, e.jurisdiction, 'Signed' FROM executive_actions e
                UNION ALL
                SELECT e.ended, e.id, e.jurisdiction, 'Ended (' || e.status || ')' FROM executive_actions e
                WHERE e.ended IS NOT NULL
            ) x
            WHERE x.date >= ? {filt}
            ORDER BY x.date DESC
            LIMIT 40
        ''', [since] + params).fetchall()
    finally:
        db.close()

    briefs = idx.briefs([r['record_id'] for r in rows])
    changes = []
    for r in rows:
        b = briefs.get(r['record_id'])
        if not b:
            continue
        changes.append({'date': r['date'], 'record_id': r['record_id'], 'identifier': b.get('identifier'),
                        'title': b['title'], 'jurisdiction': r['jurisdiction'], 'change': r['change'],
                        'current_status': b.get('status')})
    ctx.register(briefs)
    return {'since': since, 'changes': changes, 'data_as_of': idx.data_as_of}


def search_research(ctx, args):
    idx = get_index()
    results, total = idx.search(
        query=args.get('query'), level=_level(args.get('level')), topic=args.get('topic') or None,
        record_types=['research'], limit=8,
    )
    db = get_db()
    try:
        for r in results:
            r['key_findings'] = _findings(db, r['id'])
    finally:
        db.close()
    ctx.register(r['id'] for r in results)
    out = {'total_matches': total, 'results': [_for_model(r) for r in results]}
    if not results:
        out['note'] = 'No research items matched.'
    return out


# stats: every measure and grouping is a fixed SQL fragment chosen from these tables.
MEASURES = {
    'records': "m.record_type <> 'research'",
    'legislation': "m.record_type = 'legislation'",
    'enacted_legislation': "m.record_type = 'legislation' AND m.status = 'enacted'",
    'pending_legislation': "m.record_type = 'legislation' AND m.status IN ('introduced','in-committee','passed-chamber','passed-legislature')",
    'failed_legislation': "m.record_type = 'legislation' AND m.status IN ('failed','vetoed')",
    'current_documents': "m.record_type = 'document' AND m.status = 'current'",
    'active_executive_actions': "m.record_type = 'executive-action' AND m.status = 'active'",
    'research': "m.record_type = 'research'",
}
GROUP_BY = {
    'jurisdiction': ('m.jurisdiction', ''),
    'year': ('substr(m.date, 1, 4)', ''),
    'status': ('m.status', ''),
    'record_type': ('m.record_type', ''),
    'topic': ('t.topic', 'JOIN meta_topics t ON t.record_id = m.record_id'),
    'education_level': ('l.level', 'JOIN meta_levels l ON l.record_id = m.record_id'),
}


def stats(ctx, args):
    measure = args.get('measure')
    group_by = args.get('group_by')
    if measure not in MEASURES:
        raise ToolInputError(f'measure must be one of {", ".join(MEASURES)}')
    if group_by not in GROUP_BY:
        raise ToolInputError(f'group_by must be one of {", ".join(GROUP_BY)}')
    key_expr, join = GROUP_BY[group_by]
    clauses, params = [MEASURES[measure]], []
    jid = _jurisdiction(args.get('jurisdiction'))
    if jid:
        clauses.append('m.jurisdiction = ?')
        params.append(jid)
    level = _level(args.get('level'))
    if level:
        clauses.append('EXISTS (SELECT 1 FROM meta_levels l2 WHERE l2.record_id = m.record_id AND l2.level = ?)')
        params.append(level)
    if args.get('topic'):
        clauses.append('EXISTS (SELECT 1 FROM meta_topics t2 WHERE t2.record_id = m.record_id AND t2.topic = ?)')
        params.append(args['topic'])
    if args.get('record_type'):
        clauses.append('m.record_type = ?')
        params.append(args['record_type'])
    since = _date(args.get('since'), 'since')
    if since:
        clauses.append('m.date >= ?')
        params.append(since)
    until = _date(args.get('until'), 'until')
    if until:
        clauses.append('(m.date <= ? OR m.date LIKE ?)')
        params += [until, f'{until}%']

    idx = get_index()
    sql = f'''
        SELECT {key_expr} AS grp, m.record_id
        FROM meta m {join}
        WHERE {' AND '.join(clauses)}
        ORDER BY grp, m.last_activity DESC
    '''
    with idx._lock:
        rows = idx.conn.execute(sql, params).fetchall()
    groups = {}
    for r in rows:
        groups.setdefault(r['grp'] or 'unknown', []).append(r['record_id'])
    out_groups = [{'group': g, 'count': len(ids), 'record_ids': ids[:20]} for g, ids in groups.items()]
    out_groups.sort(key=lambda g: (-g['count'], str(g['group'])))
    ctx.register(i for g in out_groups for i in g['record_ids'])
    return {
        'measure': measure, 'group_by': group_by,
        'total': sum(g['count'] for g in out_groups),
        'groups': out_groups,
    }


# ---------------------------------------------------------------------------
# Dispatch and progress text
# ---------------------------------------------------------------------------

IMPLEMENTATIONS = {
    'search_records': search_records,
    'get_record': get_record,
    'provision_grid': provision_grid,
    'state_overview': state_overview,
    'recent_changes': recent_changes,
    'search_research': search_research,
    'stats': stats,
}

_TYPE_WORDS = {
    'legislation': 'legislation',
    'document': 'policy documents',
    'executive-action': 'executive actions',
    'research': 'research',
}


def run_tool(ctx, name, args):
    """Execute a tool; returns (json_text, is_error)."""
    ctx.tool_calls += 1
    impl = IMPLEMENTATIONS.get(name)
    if impl is None:
        return json.dumps({'error': f'unknown tool {name}'}), True
    if not isinstance(args, dict):
        return json.dumps({'error': 'arguments must be an object'}), True
    try:
        ctx.searched.append(describe_call(name, args))
        result = impl(ctx, args)
    except ToolInputError as e:
        return json.dumps({'error': str(e)}), True
    return json.dumps(result, ensure_ascii=False, default=str), False


def describe_call(name, args):
    """Short progress line for the interface, e.g. 'Searching Texas legislation…'."""
    idx = get_index()

    def place(value):
        jid = idx.resolve_jurisdiction(value) if value else None
        if not jid:
            return None
        return 'federal' if jid == 'us' else idx.jurisdiction_name(jid)

    if name == 'search_records':
        where = place(args.get('jurisdiction'))
        what = _TYPE_WORDS.get(args.get('record_type'), 'records')
        if where:
            return f'Searching {where} {what}…'
        return f'Searching {what}…' if what != 'records' else 'Searching Edictai records…'
    if name == 'get_record':
        b = idx.brief((args.get('id') or '').strip())
        if b:
            label = b.get('identifier') or b['title']
            return f'Opening {label}…'
        return 'Opening a record…'
    if name == 'provision_grid':
        return 'Comparing jurisdictions…'
    if name == 'state_overview':
        where = place(args.get('jurisdiction'))
        return f'Reviewing {where}…' if where else 'Reviewing a jurisdiction…'
    if name == 'recent_changes':
        where = place(args.get('jurisdiction'))
        return f'Checking recent changes in {where}…' if where else 'Checking recent changes…'
    if name == 'search_research':
        return 'Searching the research library…'
    if name == 'stats':
        return 'Counting records…'
    return 'Working…'


# ---------------------------------------------------------------------------
# Small query helpers
# ---------------------------------------------------------------------------

def _table(record_type):
    return {
        'legislation': 'legislation', 'document': 'documents',
        'executive-action': 'executive_actions', 'research': 'research',
    }[record_type]


def _one(db, sql, *params):
    row = db.execute(sql, params).fetchone()
    return row[0] if row else None


def _col(db, sql, *params):
    return [r[0] for r in db.execute(sql, params)]


def _findings(db, rid):
    return [
        {k: v for k, v in dict(r).items() if v}
        for r in db.execute(
            'SELECT finding, source_url, locator, quote FROM research_findings '
            'WHERE research_id = ? ORDER BY seq', (rid,))
    ]
