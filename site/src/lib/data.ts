/**
 * Build-time access to the Edictai dataset.
 *
 * Reads the CSV tables that `edictai build` writes to build/csv/ (one file
 * per table in db/schema.sql). Set EDICTAI_BUILD_DIR to read a build directory
 * somewhere other than ../build relative to this site.
 */
import { readFileSync, existsSync } from 'node:fs';
import { resolve, join } from 'node:path';

type Row = Record<string, string>;

const BUILD_DIR = resolve(process.env.EDICTAI_BUILD_DIR ?? resolve(process.cwd(), '..', 'build'));
const CSV_DIR = join(BUILD_DIR, 'csv');

/** RFC 4180 CSV parser: quoted fields, doubled quotes, embedded newlines, CRLF or LF. */
export function parseCsv(text: string): Row[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else quoted = false;
      } else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') { row.push(field); field = ''; }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(field); rows.push(row); row = []; field = '';
    } else field += ch;
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  const [header, ...body] = rows;
  if (!header) return [];
  return body
    .filter((r) => r.length > 1 || r[0] !== '')
    .map((r) => Object.fromEntries(header.map((h, i) => [h, r[i] ?? ''])));
}

const cache = new Map<string, Row[]>();
function table(name: string): Row[] {
  let rows = cache.get(name);
  if (!rows) {
    const file = join(CSV_DIR, `${name}.csv`);
    if (!existsSync(file)) {
      throw new Error(`Missing ${file}. Run \`uv run edictai build\` in the repository root first.`);
    }
    rows = parseCsv(readFileSync(file, 'utf8'));
    cache.set(name, rows);
  }
  return rows;
}

const orNull = (v: string | undefined) => (v === undefined || v === '' ? null : v);

function groupBy<T>(rows: T[], key: (r: T) => string): Map<string, T[]> {
  const m = new Map<string, T[]>();
  for (const r of rows) {
    const k = key(r);
    const list = m.get(k);
    if (list) list.push(r); else m.set(k, [r]);
  }
  return m;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type RecordType = 'legislation' | 'document' | 'executive-action';

export interface State {
  /** Jurisdiction id: 'us' or 'us-xx'. */
  id: string;
  /** URL code: 'us' or the lowercase USPS code, e.g. 'tx'. */
  code: string;
  name: string;
  abbreviation: string;
  classification: 'country' | 'state' | 'district';
  region: string | null;
  fips: string | null;
  legislatureUrl: string | null;
}

export interface Topic {
  code: string;
  name: string;
  description: string | null;
  position: number;
  k12: boolean;
  postsecondary: boolean;
}

export interface Source {
  url: string;
  title: string | null;
  publisher: string | null;
  kind: 'official' | 'secondary';
  accessed: string;
  archivedUrl: string | null;
}

export interface Action {
  date: string;
  actor: string | null;
  description: string;
}

export interface Provision {
  /** Provision type code from data/vocab/provision-types.yaml. */
  type: string;
  description: string;
  locator: string | null;
  sourceUrl: string | null;
}

export interface ProvisionType {
  code: string;
  name: string;
  topic: string;
  position: number;
}

export interface Organization {
  id: string;
  name: string;
  abbreviation: string | null;
  classification: string;
  jurisdiction: string | null;
  url: string | null;
}

export interface Session {
  jurisdiction: string;
  identifier: string;
  name: string;
  classification: string;
  startDate: string | null;
  endDate: string | null;
}

export interface PolicyRecord {
  id: string;
  type: RecordType;
  jurisdiction: string;
  /** URL code of the jurisdiction ('us' or 'tx'). */
  stateCode: string;
  title: string;
  /** Bill number or order number, when there is one. */
  identifier: string | null;
  classification: string;
  status: string;
  /** First action (legislation), current edition (documents), or signing date (executive actions). */
  date: string | null;
  summary: string;
  issuer: string | null;
  issuerName: string | null;
  verificationStatus: string;
  lastVerified: string | null;
  topics: string[];
  educationLevels: string[];
  sources: Source[];
  actions: Action[];
  provisions: Provision[];
  /** Every column of the record's own table, as built. */
  fields: Record<string, string | null>;
  /** Site path below the base: the record's entry on its state page, e.g. 'states/tx/#us-tx-2025-hb-1481'. */
  path: string;
}

export interface ResearchItem {
  id: string;
  title: string;
  publisher: string;
  published: string;
  classification: string;
  summary: string;
  sources: Source[];
  /** Byline, in order. */
  authors: string[];
  educationLevels: string[];
  topics: string[];
  methods: string | null;
  /** Jurisdiction ids the study covers. */
  geography: string[];
  findings: Finding[];
}

export interface Finding {
  finding: string;
  sourceUrl: string;
  locator: string | null;
  quote: string | null;
}

// ---------------------------------------------------------------------------
// Loaders
// ---------------------------------------------------------------------------

const codeFor = (jurisdictionId: string) => (jurisdictionId === 'us' ? 'us' : jurisdictionId.replace(/^us-/, ''));

let _jurisdictions: State[] | undefined;
/** The United States plus the 50 states and DC, federal first, then by name. */
export function jurisdictions(): State[] {
  return (_jurisdictions ??= table('jurisdictions')
    .map((r) => ({
      id: r.id,
      code: codeFor(r.id),
      name: r.name,
      abbreviation: r.abbreviation,
      classification: r.classification as State['classification'],
      region: orNull(r.region),
      fips: orNull(r.fips),
      legislatureUrl: orNull(r.legislature_url),
    }))
    .sort((a, b) => (a.id === 'us' ? -1 : b.id === 'us' ? 1 : a.name.localeCompare(b.name))));
}

/** The 50 states and DC. */
export function states(): State[] {
  return jurisdictions().filter((j) => j.classification !== 'country');
}

let _topics: Topic[] | undefined;
export function topics(): Topic[] {
  return (_topics ??= table('topics')
    .map((r) => ({
      code: r.code,
      name: r.name,
      description: orNull(r.description),
      position: Number(r.position),
      k12: r.k12 === '1',
      postsecondary: r.postsecondary === '1',
    }))
    .sort((a, b) => a.position - b.position));
}

function sourcesById(): Map<string, Source[]> {
  const rows = [...table('sources')].sort((a, b) => Number(a.seq) - Number(b.seq));
  const m = new Map<string, Source[]>();
  for (const r of rows) {
    const list = m.get(r.record_id) ?? [];
    list.push({
      url: r.url,
      title: orNull(r.title),
      publisher: orNull(r.publisher),
      kind: r.kind as Source['kind'],
      accessed: r.accessed,
      archivedUrl: orNull(r.archived_url),
    });
    m.set(r.record_id, list);
  }
  return m;
}

let _records: PolicyRecord[] | undefined;
/** Legislation, documents, and executive actions, newest first. */
export function records(): PolicyRecord[] {
  if (_records) return _records;
  const orgs = new Map(table('organizations').map((o) => [o.id, o.name]));
  const srcs = sourcesById();
  const tps = groupBy(table('record_topics'), (r) => r.record_id);
  const lvls = groupBy(table('record_education_levels'), (r) => r.record_id);
  const acts = groupBy(
    [...table('legislation_actions')].sort((a, b) => Number(a.seq) - Number(b.seq)),
    (r) => r.legislation_id,
  );
  const provs = groupBy(
    [...table('provisions')].sort((a, b) => Number(a.seq) - Number(b.seq)),
    (r) => r.record_id,
  );

  const make = (type: RecordType, r: Row, identifier: string | null, date: string | null): PolicyRecord => {
    const issuer = orNull(r.issuer);
    return {
      id: r.id,
      type,
      jurisdiction: r.jurisdiction,
      stateCode: codeFor(r.jurisdiction),
      title: r.title,
      identifier,
      classification: r.classification,
      status: r.status,
      date,
      summary: r.summary,
      issuer,
      issuerName: issuer ? orgs.get(issuer) ?? null : null,
      verificationStatus: r.verification_status,
      lastVerified: orNull(r.last_verified),
      topics: (tps.get(r.id) ?? []).map((t) => t.topic),
      educationLevels: (lvls.get(r.id) ?? []).map((l) => l.education_level),
      sources: srcs.get(r.id) ?? [],
      actions: (acts.get(r.id) ?? []).map((a) => ({ date: a.date, actor: orNull(a.actor), description: a.description })),
      provisions: (provs.get(r.id) ?? []).map((p) => ({
        type: p.type,
        description: p.description,
        locator: orNull(p.locator),
        sourceUrl: orNull(p.source_url),
      })),
      fields: Object.fromEntries(Object.entries(r).map(([k, v]) => [k, orNull(v)])),
      path: `states/${codeFor(r.jurisdiction)}/#${r.id}`,
    };
  };

  const all = [
    ...table('legislation').map((r) => make('legislation', r, r.identifier, orNull(r.introduced_date))),
    ...table('documents').map((r) => make('document', r, null, orNull(r.updated) ?? orNull(r.published))),
    ...table('executive_actions').map((r) => make('executive-action', r, orNull(r.number), r.signed)),
  ];
  all.sort((a, b) => (b.date ?? '').localeCompare(a.date ?? '') || a.id.localeCompare(b.id));
  return (_records = all);
}

export function recordsOfType(type: RecordType): PolicyRecord[] {
  return records().filter((r) => r.type === type);
}

export function recordsFor(jurisdictionId: string): PolicyRecord[] {
  return records().filter((r) => r.jurisdiction === jurisdictionId);
}

export function recordsForTopic(code: string): PolicyRecord[] {
  return records().filter((r) => r.topics.includes(code));
}

let _research: ResearchItem[] | undefined;
/** Research items, newest first. */
export function research(): ResearchItem[] {
  if (_research) return _research;
  const srcs = sourcesById();
  const bySeq = (rows: Row[]) => [...rows].sort((a, b) => Number(a.seq) - Number(b.seq));
  const auth = groupBy(bySeq(table('research_authors')), (r) => r.research_id);
  const geo = groupBy(table('research_geography'), (r) => r.research_id);
  const kf = groupBy(bySeq(table('research_findings')), (r) => r.research_id);
  const tps = groupBy(table('record_topics'), (r) => r.record_id);
  const lvls = groupBy(table('record_education_levels'), (r) => r.record_id);
  return (_research = table('research')
    .map((r) => ({
      id: r.id,
      title: r.title,
      publisher: r.publisher,
      published: r.published,
      classification: r.classification,
      summary: r.summary,
      sources: srcs.get(r.id) ?? [],
      authors: (auth.get(r.id) ?? []).map((a) => a.name),
      educationLevels: (lvls.get(r.id) ?? []).map((l) => l.education_level),
      topics: (tps.get(r.id) ?? []).map((t) => t.topic),
      methods: orNull(r.methods),
      geography: (geo.get(r.id) ?? []).map((g) => g.jurisdiction),
      findings: (kf.get(r.id) ?? []).map((f) => ({
        finding: f.finding,
        sourceUrl: f.source_url,
        locator: orNull(f.locator),
        quote: orNull(f.quote),
      })),
    }))
    .sort((a, b) => b.published.localeCompare(a.published) || a.title.localeCompare(b.title)));
}

let _provisionTypes: ProvisionType[] | undefined;
/** The provision-type vocabulary in its published order. */
export function provisionTypes(): ProvisionType[] {
  return (_provisionTypes ??= table('provision_types')
    .map((r) => ({ code: r.code, name: r.name, topic: r.topic, position: Number(r.position) }))
    .sort((a, b) => a.position - b.position));
}

let _organizations: Organization[] | undefined;
export function organizations(): Organization[] {
  return (_organizations ??= table('organizations').map((r) => ({
    id: r.id,
    name: r.name,
    abbreviation: orNull(r.abbreviation),
    classification: r.classification,
    jurisdiction: orNull(r.jurisdiction),
    url: orNull(r.url),
  })));
}

/** A jurisdiction's legislative sessions, most recent first. */
export function sessionsFor(jurisdictionId: string): Session[] {
  return table('sessions')
    .filter((r) => r.jurisdiction === jurisdictionId)
    .map((r) => ({
      jurisdiction: r.jurisdiction,
      identifier: r.identifier,
      name: r.name,
      classification: r.classification,
      startDate: orNull(r.start_date),
      endDate: orNull(r.end_date),
    }))
    .sort((a, b) => (b.startDate ?? b.identifier).localeCompare(a.startDate ?? a.identifier));
}

let _asOf: string | undefined;
/** The most recent date any record was verified or any source accessed: how current the data is. */
export function dataAsOf(): string {
  if (_asOf) return _asOf;
  const dates = [
    ...records().map((r) => r.lastVerified ?? ''),
    ...table('sources').map((s) => s.accessed),
  ].filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d));
  return (_asOf = dates.sort().at(-1) ?? '');
}

/** Headline counts used on the home page. */
export function counts() {
  return {
    states: states().length,
    records: records().length,
    topics: topics().length,
    legislation: recordsOfType('legislation').length,
    documents: recordsOfType('document').length,
    executiveActions: recordsOfType('executive-action').length,
    research: research().length,
  };
}

/** Readable label for a record's kind, e.g. 'Bill', 'Guidance', 'Executive order'. */
export function kindLabel(r: Pick<PolicyRecord, 'classification'>): string {
  const s = r.classification.replace(/-/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Join a site-relative path onto the configured base, e.g. url('states/tx/') -> '/states/tx/'. */
export function url(path = ''): string {
  const base = import.meta.env.BASE_URL.replace(/\/?$/, '/');
  return base + path.replace(/^\//, '');
}
