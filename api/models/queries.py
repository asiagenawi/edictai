"""Read queries for the API.

v_policies_legacy and v_states_legacy (db/schema.sql) present the built
database in the field names and status vocabulary these endpoints have always
returned, so the routes did not change when the data moved to data/.
"""

from models.database import get_db

POLICIES = 'v_policies_legacy'

# Columns the endpoints return for a policy; record_id is the permanent string id.
POLICY_COLUMNS = '''
    p.id, p.record_id, p.state_id, p.title, p.description, p.policy_type, p.level,
    p.status, p.date_introduced, p.date_enacted, p.bill_number, p.sponsor,
    p.summary_text, p.source_url
'''


def get_all_states():
    db = get_db()
    rows = db.execute('''
        SELECT id, name, code, fips, region, has_ai_guidance, policy_count, policy_status
        FROM v_states_legacy
        ORDER BY name
    ''').fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_state_policies(state_code):
    db = get_db()
    rows = db.execute(f'''
        SELECT {POLICY_COLUMNS} FROM {POLICIES} p
        WHERE p.state_code = ?
        ORDER BY p.date_introduced DESC
    ''', (state_code.upper(),)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_all_policies(level=None, status=None, topic_id=None, limit=50, offset=0):
    db = get_db()
    query = f'SELECT {POLICY_COLUMNS} FROM {POLICIES} p'
    params = []
    conditions = []

    if topic_id:
        query += '''
            JOIN record_topics rt ON rt.record_id = p.record_id
            JOIN topics t ON t.code = rt.topic
        '''
        conditions.append('t.position = ?')
        params.append(topic_id)

    if level:
        conditions.append('p.level = ?')
        params.append(level)

    if status:
        conditions.append('p.status = ?')
        params.append(status)

    if conditions:
        query += ' WHERE ' + ' AND '.join(conditions)

    query += ' ORDER BY p.date_introduced DESC LIMIT ? OFFSET ?'
    params.extend([limit, offset])

    rows = db.execute(query, params).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_policy_by_id(policy_id):
    db = get_db()
    row = db.execute(
        f'SELECT {POLICY_COLUMNS} FROM {POLICIES} p WHERE p.id = ?', (policy_id,)
    ).fetchone()
    db.close()
    return dict(row) if row else None


def get_timeline_data(state_code=None, topic_id=None, policy_type=None):
    db = get_db()
    query = f'''
        SELECT substr(p.date_introduced, 1, 4) as year,
               COUNT(*) as count
        FROM {POLICIES} p
    '''
    joins = []
    conditions = ['p.date_introduced IS NOT NULL']
    params = []

    if state_code:
        conditions.append('p.state_code = ?')
        params.append(state_code.upper())

    if topic_id:
        joins.append('JOIN record_topics rt ON rt.record_id = p.record_id')
        joins.append('JOIN topics t ON t.code = rt.topic')
        conditions.append('t.position = ?')
        params.append(topic_id)

    if policy_type:
        conditions.append('p.policy_type = ?')
        params.append(policy_type)

    query = query + ' '.join(joins)
    query += ' WHERE ' + ' AND '.join(conditions)
    query += ' GROUP BY year ORDER BY year'

    rows = db.execute(query, params).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_topic_counts(state_code=None, policy_type=None):
    db = get_db()
    conditions = []
    params = []

    if state_code:
        conditions.append('p.state_code = ?')
        params.append(state_code.upper())

    if policy_type:
        conditions.append('p.policy_type = ?')
        params.append(policy_type)

    where = ('AND ' + ' AND '.join(conditions)) if conditions else ''
    # Filters sit in the join so topics with no matching policies still appear with 0.
    rows = db.execute(f'''
        SELECT t.name, COUNT(p.id) as count
        FROM topics t
        LEFT JOIN record_topics rt ON rt.topic = t.code
        LEFT JOIN {POLICIES} p ON p.record_id = rt.record_id {where}
        GROUP BY t.code
        ORDER BY count DESC
    ''', params).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_status_breakdown():
    db = get_db()
    rows = db.execute(f'''
        SELECT status, COUNT(*) as count
        FROM {POLICIES}
        GROUP BY status
        ORDER BY count DESC
    ''').fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_level_breakdown():
    db = get_db()
    rows = db.execute(f'''
        SELECT level, COUNT(*) as count
        FROM {POLICIES}
        GROUP BY level
        ORDER BY count DESC
    ''').fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_all_topics():
    db = get_db()
    rows = db.execute(
        'SELECT position as id, name, description FROM topics ORDER BY name'
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def search_policies(query_text, limit=10):
    db = get_db()
    pattern = f'%{query_text}%'
    rows = db.execute(f'''
        SELECT {POLICY_COLUMNS}, p.state_name, p.state_code
        FROM {POLICIES} p
        WHERE p.title LIKE ? OR p.description LIKE ? OR p.summary_text LIKE ?
        ORDER BY p.date_introduced DESC
        LIMIT ?
    ''', (pattern, pattern, pattern, limit)).fetchall()
    db.close()
    return [dict(r) for r in rows]
