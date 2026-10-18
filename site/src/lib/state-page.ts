/**
 * Everything a state page shows, derived from the built dataset.
 *
 * Labels come from the vocabularies (data/vocab/enums.yaml); counts, dates and
 * text come from the records. Nothing here is written by hand per state.
 */
import {
  organizations,
  provisionTypes,
  recordsFor,
  sessionsFor,
  type PolicyRecord,
  type State,
} from './data';

export const LEVELS: Record<string, string> = { k12: 'K-12', postsecondary: 'Higher ed' };

const TYPE_LABELS: Record<string, string> = {
  bill: 'Bill',
  resolution: 'Resolution',
  'joint-resolution': 'Joint resolution',
  'concurrent-resolution': 'Concurrent resolution',
  'constitutional-amendment': 'Constitutional amendment',
  appropriation: 'Appropriation',
  guidance: 'Guidance',
  policy: 'Policy',
  framework: 'Framework',
  toolkit: 'Toolkit',
  statement: 'Statement',
  report: 'Report',
  'model-policy': 'Model policy',
  program: 'Program',
  'executive-order': 'Executive order',
  directive: 'Directive',
  proclamation: 'Proclamation',
  memorandum: 'Memorandum',
};

const STATUS_LABELS: Record<string, string> = {
  introduced: 'Introduced',
  'in-committee': 'In committee',
  'passed-chamber': 'Passed one chamber',
  'passed-legislature': 'Passed legislature',
  enacted: 'Became law',
  vetoed: 'Vetoed',
  failed: 'Did not pass',
  current: 'Current',
  withdrawn: 'Withdrawn',
  active: 'In force',
  rescinded: 'Rescinded',
  expired: 'Expired',
  superseded: 'Superseded',
};

/**
 * The register's type marks, drawn from the brand shapes: legislation is a red
 * circle, executive actions a green diamond, and each kind of document its own
 * shape and colour. Paths are in a 12-unit box.
 */
const GLYPHS: Record<string, string> = {
  legislation: '<circle cx="6" cy="6" r="5.5" fill="var(--red)"/>',
  'executive-action': '<path d="M6 0l6 6-6 6-6-6z" fill="var(--green)"/>',
  'model-policy': '<rect x=".5" y=".5" width="11" height="11" fill="var(--blue)"/>',
  guidance: '<path d="M.5 11.5V.5a11 11 0 0 1 11 11z" fill="var(--sky)"/>',
  policy: '<path d="M.5 7.5a5.5 5.5 0 0 1 11 0z" fill="var(--yellow)"/>',
  framework: '<path d="M6 .5l5.5 11H.5z" fill="var(--mint)"/>',
  toolkit: '<rect x=".5" y=".5" width="11" height="11" fill="var(--butter)"/>',
  report: '<circle cx="6" cy="6" r="5.5" fill="var(--pink)"/>',
  statement: '<path d="M.5 7.5a5.5 5.5 0 0 1 11 0z" fill="var(--pink)"/>',
  program: '<path d="M6 0l6 6-6 6-6-6z" fill="var(--butter)"/>',
};

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** '2026-09-10' -> '10 Sep 2026'; partial dates keep only what they have. */
export function longDate(d: string | null): string {
  if (!d) return '';
  const [y, m, day] = d.split('-');
  return [day ? String(+day) : '', m ? MONTHS[+m - 1] : '', y].filter(Boolean).join(' ');
}

/** Day and month for the register; blank when the record has only a year (or year and month: month only). */
function shortDate(d: string | null): string {
  if (!d) return '';
  const [, m, day] = d.split('-');
  return day ? `${+day} ${MONTHS[+m - 1]}` : m ? MONTHS[+m - 1] : '';
}

export interface Row {
  id: string;
  ident: string | null;
  title: string;
  type: string;
  glyph: string;
  /** Matrix dot colour: law (legislation), doc, or order. */
  kind: 'law' | 'doc' | 'order';
  levels: string[];
  status: string;
  date: string | null;
  year: string;
  day: string;
  sub: string;
  summary: string;
  provisions: { type: string; name: string; locator: string | null; description: string }[];
  facts: [string, string][];
  /** Official sources (or, when a record has none, its secondary ones), with archived copies. */
  sources: { url: string; title: string; archivedUrl: string | null }[];
  /** Legislation only: the most recent recorded action. */
  latestAction: { date: string; description: string } | null;
  actions: { date: string; description: string }[];
}

/** The date a record is filed under: its latest status for legislation, current edition for documents, signing for orders. */
function registerDate(r: PolicyRecord): string | null {
  if (r.type !== 'legislation') return r.date;
  return r.fields.status_date ?? r.fields.last_action_date ?? r.fields.introduced_date ?? null;
}

function row(r: PolicyRecord, orgs: Map<string, { name: string; abbreviation: string | null }>): Row {
  const ptNames = new Map(provisionTypes().map((p) => [p.code, p.name]));
  const type = TYPE_LABELS[r.classification] ?? r.classification;
  const issuer = r.issuer ? orgs.get(r.issuer) : undefined;
  const ident = r.identifier ?? issuer?.abbreviation ?? null;
  const date = registerDate(r);
  const used = [...new Set(r.provisions.map((p) => p.type))];
  const fallback = r.type === 'legislation' ? `${r.fields.session ?? ''} session`.trim() : issuer?.name ?? '';
  const f = r.fields;
  const facts: [string, string][] = [['Record ID', r.id]];
  if (r.type === 'legislation') {
    if (f.session) facts.push(['Session', f.session]);
    if (f.introduced_date) facts.push(['Introduced', longDate(f.introduced_date)]);
    if (f.enacted_date) facts.push(['Enacted', longDate(f.enacted_date)]);
    if (f.session_law) facts.push(['Session law', f.session_law]);
  } else {
    if (issuer) facts.push(['Issuer', issuer.name]);
    if (r.type === 'document' && f.published) facts.push(['Published', longDate(f.published)]);
    if (r.type === 'document' && f.updated) facts.push(['Updated', longDate(f.updated)]);
    if (r.type === 'executive-action' && f.signed) facts.push(['Signed', longDate(f.signed)]);
  }
  if (r.educationLevels.length) facts.push(['Level', r.educationLevels.map((l) => LEVELS[l] ?? l).join(', ')]);
  if (r.topics.length) facts.push(['Topics', r.topics.join(', ')]);
  const official = r.sources.filter((s) => s.kind === 'official');

  return {
    id: r.id,
    ident,
    // Display only: some official titles end with a period.
    title: r.title.replace(/\.$/, ''),
    type,
    glyph: GLYPHS[r.type === 'document' ? r.classification : r.type] ?? GLYPHS.guidance,
    kind: r.type === 'legislation' ? 'law' : r.type === 'executive-action' ? 'order' : 'doc',
    levels: r.educationLevels,
    status: STATUS_LABELS[r.status] ?? r.status,
    date,
    year: date ? date.slice(0, 4) : 'Undated',
    day: shortDate(date),
    sub: used.length ? used.map((c) => ptNames.get(c) ?? c).join(' · ') : fallback,
    summary: r.summary,
    provisions: r.provisions.map((p) => ({ type: p.type, name: ptNames.get(p.type) ?? p.type, locator: p.locator, description: p.description })),
    facts,
    sources: (official.length ? official : r.sources).map((s) => ({ url: s.url, title: s.title ?? s.publisher ?? s.url, archivedUrl: s.archivedUrl })),
    latestAction: r.type === 'legislation' && r.actions.length ? { date: r.actions.at(-1)!.date, description: r.actions.at(-1)!.description } : null,
    actions: r.actions.map((a) => ({ date: a.date, description: a.description })),
  };
}

const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve',
  'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty'];
const word = (n: number) => WORDS[n] ?? String(n);
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** "a law", "two pending bills", "an executive order". */
function phrase(n: number, singular: string, plural: string): string {
  if (n === 1) return `${/^[aeiou]/.test(singular) ? 'an' : 'a'} ${singular}`;
  return `${word(n)} ${plural}`;
}

function listJoin(parts: string[]): string {
  return parts.length < 2 ? parts.join('') : `${parts.slice(0, -1).join(', ')} and ${parts.at(-1)}`;
}

const DOC_PHRASES: [string, string, string][] = [
  ['model-policy', 'model policy', 'model policies'],
  ['policy', 'policy', 'policies'],
  ['guidance', 'guidance document', 'guidance documents'],
  ['framework', 'framework', 'frameworks'],
  ['toolkit', 'toolkit', 'toolkits'],
  ['report', 'report', 'reports'],
  ['statement', 'statement', 'statements'],
  ['program', 'program', 'programs'],
];

/** One sentence saying what the jurisdiction has, from counts by type. */
function lede(state: State, list: PolicyRecord[]): string {
  if (!list.length) return `No records for ${state.name} yet.`;
  const levels = new Set(list.flatMap((r) => r.educationLevels));
  const where = levels.has('k12') && levels.has('postsecondary') ? 'K-12 and higher education'
    : levels.has('postsecondary') ? 'higher education' : levels.has('k12') ? 'K-12 education' : 'education';
  const leg = list.filter((r) => r.type === 'legislation');
  const enacted = leg.filter((r) => r.status === 'enacted').length;
  const ended = leg.filter((r) => r.status === 'failed' || r.status === 'vetoed').length;
  const pending = leg.length - enacted - ended;
  const parts = [
    enacted && phrase(enacted, 'law', 'laws'),
    pending && phrase(pending, 'pending bill', 'pending bills'),
    ended && phrase(ended, 'bill that did not pass', 'bills that did not pass'),
    ...DOC_PHRASES.map(([c, s, p]) => {
      const n = list.filter((r) => r.type === 'document' && r.classification === c).length;
      return n && phrase(n, s, p);
    }),
    (() => {
      const n = list.filter((r) => r.type === 'executive-action').length;
      return n && phrase(n, 'executive order', 'executive orders');
    })(),
  ].filter(Boolean) as string[];
  const n = list.length;
  return `${cap(word(n))} ${n === 1 ? 'record' : 'records'} on artificial intelligence in ${where}: ${listJoin(parts)}.`;
}

/** Register rows for any list of records, newest first (the state pages and Bills & policies share them). */
export function registerRows(list: PolicyRecord[]): Row[] {
  const orgs = new Map(organizations().map((o) => [o.id, o]));
  return list.map((r) => row(r, orgs)).sort((a, b) => (b.date ?? '').localeCompare(a.date ?? '') || a.title.localeCompare(b.title));
}

export function statePage(state: State) {
  const list = recordsFor(state.id);
  const orgList = organizations();
  const orgs = new Map(orgList.map((o) => [o.id, o]));
  const rows = registerRows(list);

  const years = [...new Set(rows.map((r) => r.year))];
  const groups = years.map((year) => ({ year, rows: rows.filter((r) => r.year === year) }));

  // Issuers: the jurisdiction's own bodies plus any outside body that issued one of its records.
  // Legislation counts toward the jurisdiction's legislature.
  const legislature = orgList.find((o) => o.jurisdiction === state.id && o.classification === 'legislature');
  const issuerOf = (r: PolicyRecord) => (r.type === 'legislation' ? legislature?.id : r.issuer) ?? null;
  const issuerIds = new Set([...orgList.filter((o) => o.jurisdiction === state.id).map((o) => o.id), ...list.map(issuerOf).filter(Boolean) as string[]]);
  const issuers = [...issuerIds]
    .map((id) => ({ org: orgs.get(id)!, count: list.filter((r) => issuerOf(r) === id).length }))
    .filter((i) => i.org)
    .sort((a, b) => b.count - a.count || a.org.name.localeCompare(b.org.name));

  const types = provisionTypes();
  const usedTypes = new Set(list.flatMap((r) => r.provisions.map((p) => p.type)));
  const leg = list.filter((r) => r.type === 'legislation');
  const ended = leg.filter((r) => r.status === 'failed' || r.status === 'vetoed').length;
  const docCount = (c: string) => list.filter((r) => r.type === 'document' && r.classification === c).length;
  const recordFacts: [string, string][] = [
    ['Total', String(list.length)],
    ['Laws', String(leg.filter((r) => r.status === 'enacted').length)],
    ['Bills pending', String(leg.filter((r) => !['enacted', 'failed', 'vetoed'].includes(r.status)).length)],
    ...(ended ? [['Bills that did not pass', String(ended)] as [string, string]] : []),
    ...DOC_PHRASES.filter(([c]) => docCount(c)).map(([c, , p]) => [cap(p), String(docCount(c))] as [string, string]),
    ['Executive orders', String(list.filter((r) => r.type === 'executive-action').length)],
    ['Provision types used', `${usedTypes.size} of ${types.length}`],
  ];

  return {
    rows,
    groups,
    lede: lede(state, list),
    issuers,
    sessions: sessionsFor(state.id),
    provisionTypes: types,
    usedTypes,
    recordFacts,
    provisionCount: list.reduce((n, r) => n + r.provisions.length, 0),
    actionCount: list.reduce((n, r) => n + r.actions.length, 0),
    facets: {
      type: [...new Set(rows.map((r) => r.type))],
      level: ['k12', 'postsecondary'].filter((l) => rows.some((r) => r.levels.includes(l))),
      status: [...new Set(rows.map((r) => r.status))],
      year: years,
    },
  };
}
