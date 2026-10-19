"""Full-text index over the built database, held in memory.

The build database is opened read-only, so the FTS5 index lives in a private
in-memory database built on first use: one row per record covering its title,
identifier, summary, provisions, and legislative action text (research items
add their findings). A `meta` table beside it carries the exact-match fields
the search filters use, so a filtered search is a single query.
"""

import re
import sqlite3
import threading

from models.database import get_db

POLICY_TYPES = ('legislation', 'document', 'executive-action')
RECORD_TYPES = POLICY_TYPES + ('research',)
LEVELS = ('k12', 'postsecondary')

_STOPWORDS = frozenset('''
a an and any are as at be been being but by can could did do does for from had has have
how i if in into is it its me my of on or our should so than that the their them there
these they this those to was we were what when where which who why will with would you
your about also all each more most some such not no state states law laws bill bills
policy policies'''.split())

_IDENT_RE = re.compile(r'\b([a-z]{1,4})\.?\s?(\d{1,5})\b', re.I)


def compact_identifier(value):
    """'H.R. 6791' -> 'HR6791', 'N-6-26' -> 'N626': spacing and punctuation never decide a match."""
    return re.sub(r'[^A-Za-z0-9]', '', value or '').upper()


def plain_verification(status):
    """Readers see one word: machine- and human-verified records are both "verified"."""
    return 'verified' if status and status.endswith('-verified') else status


def normalize_level(value):
    if not value:
        return None
    v = re.sub(r'[^a-z0-9]', '', str(value).lower())
    if v in ('k12', 'k', 'school', 'schools', 'primary', 'secondary'):
        return 'k12'
    if v in ('postsecondary', 'higher', 'highered', 'highereducation', 'college', 'university'):
        return 'postsecondary'
    return None


class SearchIndex:
    def __init__(self):
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(':memory:', check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.jurisdictions = {}  # id -> {'id', 'name', 'abbreviation', 'classification'}
        self._jur_lookup = {}  # lowercase id / abbreviation / name -> id
        self.data_as_of = None
        self._build()

    # -- building -----------------------------------------------------------

    def _build(self):
        src = get_db()
        try:
            self._load_jurisdictions(src)
            self._create_tables()
            self._load_policies(src)
            self._load_research(src)
            self.data_as_of = src.execute('''
                SELECT MAX(d) FROM (
                    SELECT MAX(last_verified) AS d FROM legislation
                    UNION ALL SELECT MAX(last_verified) FROM documents
                    UNION ALL SELECT MAX(last_verified) FROM executive_actions
                    UNION ALL SELECT MAX(last_verified) FROM research
                )
            ''').fetchone()[0]
        finally:
            src.close()
        self.conn.commit()

    def _load_jurisdictions(self, src):
        for r in src.execute('SELECT id, name, abbreviation, classification FROM jurisdictions'):
            j = dict(r)
            self.jurisdictions[j['id']] = j
            for key in (j['id'], j['abbreviation'], j['name']):
                self._jur_lookup[key.lower()] = j['id']
        for alias in ('federal', 'united states', 'usa', 'u.s.', 'congress', 'national'):
            self._jur_lookup[alias] = 'us'
        if 'us-dc' in self.jurisdictions:
            self._jur_lookup['washington dc'] = 'us-dc'
            self._jur_lookup['washington, dc'] = 'us-dc'

    def _create_tables(self):
        c = self.conn
        c.executescript('''
            CREATE VIRTUAL TABLE fts USING fts5(
                record_id UNINDEXED, title, identifier, summary, body,
                tokenize = 'porter unicode61'
            );
            CREATE TABLE meta (
                record_id TEXT PRIMARY KEY,
                record_type TEXT NOT NULL,
                jurisdiction TEXT,
                identifier TEXT,
                title TEXT NOT NULL,
                classification TEXT,
                status TEXT,
                is_current INTEGER,
                date TEXT,
                last_activity TEXT,
                summary TEXT,
                source_url TEXT,
                num_id INTEGER,
                verification_status TEXT,
                publisher TEXT
            );
            CREATE TABLE meta_levels (record_id TEXT, level TEXT, PRIMARY KEY (record_id, level));
            CREATE TABLE meta_topics (record_id TEXT, topic TEXT, PRIMARY KEY (record_id, topic));
            CREATE TABLE meta_geo (record_id TEXT, jurisdiction TEXT, PRIMARY KEY (record_id, jurisdiction));
        ''')

    def _load_policies(self, src):
        provisions = {}
        for r in src.execute('SELECT record_id, description, quote FROM provisions ORDER BY record_id, seq'):
            provisions.setdefault(r['record_id'], []).append(
                ' '.join(x for x in (r['description'], r['quote']) if x)
            )
        actions = {}
        for r in src.execute('SELECT legislation_id, description FROM legislation_actions ORDER BY legislation_id, seq'):
            actions.setdefault(r['legislation_id'], []).append(r['description'])
        activity = {}
        for r in src.execute('SELECT id, last_action_date AS d FROM legislation'):
            activity[r['id']] = r['d']
        for r in src.execute('SELECT id, COALESCE(updated, published) AS d FROM documents'):
            activity[r['id']] = r['d']
        for r in src.execute('SELECT id, COALESCE(ended, signed) AS d FROM executive_actions'):
            activity[r['id']] = r['d']

        rows = src.execute('''
            SELECT num_id, id, record_type, jurisdiction, identifier, title, classification,
                   status, is_current, date, summary, abstract, issuer_name, sponsors,
                   source_url, verification_status
            FROM v_policies
        ''').fetchall()
        for r in rows:
            rid = r['id']
            ident = r['identifier'] or ''
            self.conn.execute(
                'INSERT INTO meta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (rid, r['record_type'], r['jurisdiction'], r['identifier'], r['title'],
                 r['classification'], r['status'], r['is_current'], r['date'],
                 activity.get(rid) or r['date'], r['summary'], r['source_url'], r['num_id'],
                 r['verification_status'], r['issuer_name']),
            )
            jname = self.jurisdictions.get(r['jurisdiction'], {}).get('name', '')
            body = ' '.join(
                [r['abstract'] or '', r['issuer_name'] or '', r['sponsors'] or '', jname,
                 r['classification'] or '']
                + provisions.get(rid, []) + actions.get(rid, [])
            )
            self.conn.execute(
                'INSERT INTO fts (record_id, title, identifier, summary, body) VALUES (?,?,?,?,?)',
                (rid, r['title'], f'{ident} {compact_identifier(ident)}', r['summary'] or '', body),
            )
        self._load_facets(src)

    def _load_facets(self, src):
        self.conn.executemany(
            'INSERT OR IGNORE INTO meta_levels VALUES (?, ?)',
            [tuple(r) for r in src.execute('SELECT record_id, education_level FROM record_education_levels')],
        )
        self.conn.executemany(
            'INSERT OR IGNORE INTO meta_topics VALUES (?, ?)',
            [tuple(r) for r in src.execute('SELECT record_id, topic FROM record_topics')],
        )

    def _load_research(self, src):
        findings = {}
        for r in src.execute('SELECT research_id, finding, quote FROM research_findings ORDER BY research_id, seq'):
            findings.setdefault(r['research_id'], []).append(
                ' '.join(x for x in (r['finding'], r['quote']) if x)
            )
        rows = src.execute('''
            SELECT rs.id, rec.num_id, rs.title, rs.publisher, rs.published, rs.classification,
                   rs.summary, rs.methods, rs.verification_status,
                   (SELECT url FROM sources s WHERE s.record_id = rs.id
                    ORDER BY s.kind <> 'official', s.seq LIMIT 1) AS source_url
            FROM research rs JOIN records rec ON rec.id = rs.id
        ''').fetchall()
        for r in rows:
            self.conn.execute(
                'INSERT INTO meta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (r['id'], 'research', None, None, r['title'], r['classification'], None, None,
                 r['published'], r['published'], r['summary'], r['source_url'], r['num_id'],
                 r['verification_status'], r['publisher']),
            )
            body = ' '.join([r['publisher'] or '', r['methods'] or ''] + findings.get(r['id'], []))
            self.conn.execute(
                'INSERT INTO fts (record_id, title, identifier, summary, body) VALUES (?,?,?,?,?)',
                (r['id'], r['title'], '', r['summary'] or '', body),
            )
        self.conn.executemany(
            'INSERT OR IGNORE INTO meta_geo VALUES (?, ?)',
            [tuple(r) for r in src.execute('SELECT research_id, jurisdiction FROM research_geography')],
        )

    # -- lookups ------------------------------------------------------------

    def resolve_jurisdiction(self, value):
        """'TX', 'Texas', 'us-tx' -> 'us-tx'. None when unrecognised."""
        if not value:
            return None
        return self._jur_lookup.get(str(value).strip().lower())

    def jurisdiction_name(self, jid):
        if jid == 'us':
            return 'federal'
        return self.jurisdictions.get(jid, {}).get('name', jid)

    def brief(self, record_id):
        with self._lock:
            row = self.conn.execute('SELECT * FROM meta WHERE record_id = ?', (record_id,)).fetchone()
        return self._brief(row) if row else None

    def briefs(self, record_ids):
        ids = list(dict.fromkeys(record_ids))
        if not ids:
            return {}
        with self._lock:
            rows = self.conn.execute(
                f'SELECT * FROM meta WHERE record_id IN ({",".join("?" * len(ids))})', ids
            ).fetchall()
        return {r['record_id']: self._brief(r) for r in rows}

    def _brief(self, row):
        summary = row['summary'] or ''
        if len(summary) > 240:
            summary = summary[:237].rsplit(' ', 1)[0] + '...'
        return {
            'id': row['record_id'],
            'type': row['record_type'],
            'jurisdiction': row['jurisdiction'],
            'identifier': row['identifier'],
            'title': row['title'],
            'classification': row['classification'],
            'status': row['status'],
            'date': row['date'],
            'last_activity': row['last_activity'],
            'verification': plain_verification(row['verification_status']),
            'summary': summary,
            'source_url': row['source_url'],
            'num_id': row['num_id'],
            'publisher': row['publisher'],
        }

    def exists(self, record_id):
        with self._lock:
            return self.conn.execute(
                'SELECT 1 FROM meta WHERE record_id = ?', (record_id,)
            ).fetchone() is not None

    def find_policy(self, jurisdiction=None, identifier=None, title=None):
        """True when the dataset already has a record matching a claimed policy."""
        clauses, params = [], []
        if jurisdiction:
            clauses.append('jurisdiction = ?')
            params.append(jurisdiction)
        where = ' AND '.join(clauses) or '1'
        with self._lock:
            rows = self.conn.execute(
                f'SELECT identifier, title FROM meta WHERE {where}', params
            ).fetchall()
        want_ident = compact_identifier(identifier)
        want_title = (title or '').strip().lower()
        for r in rows:
            if want_ident and compact_identifier(r['identifier']) == want_ident:
                return True
            if want_title and r['title'].strip().lower() == want_title:
                return True
        return False

    # -- search -------------------------------------------------------------

    @staticmethod
    def fts_query(text):
        """Turn free text into a safe FTS5 expression: quoted terms joined with OR."""
        text = text or ''
        terms = []
        prefixes = set()
        for m in _IDENT_RE.finditer(text):
            terms.append((m.group(1) + m.group(2)).lower())
            prefixes.add(m.group(1).lower())
        for tok in re.findall(r'[A-Za-z0-9]+', text.lower()):
            # A bare 'hb' matches every House bill; the compact 'hb1481' carries the match.
            if tok in _STOPWORDS or tok in prefixes:
                continue
            if len(tok) < 2 and not tok.isdigit():
                continue
            terms.append(tok)
        terms = list(dict.fromkeys(terms))[:24]
        return ' OR '.join(f'"{t}"' for t in terms)

    def search(self, query=None, jurisdiction=None, level=None, topic=None, record_types=None,
               status=None, current_only=False, since=None, until=None, limit=10):
        """Ranked matches as briefs, plus the total match count before the limit."""
        clauses, params = [], []
        match = self.fts_query(query)
        if jurisdiction:
            clauses.append('(m.jurisdiction = ? OR EXISTS (SELECT 1 FROM meta_geo g '
                           'WHERE g.record_id = m.record_id AND g.jurisdiction = ?))')
            params += [jurisdiction, jurisdiction]
        if level:
            clauses.append('EXISTS (SELECT 1 FROM meta_levels l WHERE l.record_id = m.record_id AND l.level = ?)')
            params.append(level)
        if topic:
            clauses.append('EXISTS (SELECT 1 FROM meta_topics t WHERE t.record_id = m.record_id AND t.topic = ?)')
            params.append(topic)
        if record_types:
            clauses.append(f'm.record_type IN ({",".join("?" * len(record_types))})')
            params += list(record_types)
        if status:
            clauses.append('m.status = ?')
            params.append(status)
        if current_only:
            clauses.append('m.is_current = 1')
        if since:
            clauses.append('m.date >= ?')
            params.append(since)
        if until:
            # Dates may be YYYY or YYYY-MM; anything starting with the bound is inside it.
            clauses.append('(m.date <= ? OR m.date LIKE ?)')
            params += [until, f'{until}%']

        where = ' AND '.join(clauses) or '1'
        if match:
            sql_from = 'fts JOIN meta m ON m.record_id = fts.record_id'
            where = f'fts MATCH ? AND {where}'
            params = [match] + params
            order = 'bm25(fts, 0.0, 10.0, 8.0, 3.0, 1.0)'
        else:
            sql_from = 'meta m'
            order = 'm.last_activity DESC'
        with self._lock:
            total = self.conn.execute(f'SELECT COUNT(*) FROM {sql_from} WHERE {where}', params).fetchone()[0]
            rows = self.conn.execute(
                f'SELECT m.* FROM {sql_from} WHERE {where} ORDER BY {order} LIMIT ?',
                params + [limit],
            ).fetchall()
        return [self._brief(r) for r in rows], total


_index = None
_index_lock = threading.Lock()


def get_index():
    global _index
    if _index is None:
        with _index_lock:
            if _index is None:
                _index = SearchIndex()
    return _index


def reset_index():
    """Drop the cached index (tests, or after the database is rebuilt)."""
    global _index
    with _index_lock:
        _index = None
