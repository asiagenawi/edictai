/**
 * Articles, resolved against the data: each note's jurisdiction, identifier, title, status,
 * locator, quote and link come from the records and research library as built.
 */
import { getCollection, type CollectionEntry } from 'astro:content';
import type { Dataset, Row } from './dataset';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { jurisdictions, parseCsv, records, research, topics, url, type PolicyRecord } from './data';
import { LEVELS, STATUS_LABELS, longDate } from './state-page';

export type ArticleType = 'Explainer' | 'Analysis' | 'Roundup' | 'State brief' | 'Research digest' | 'Opinion';
export const KINDS: ArticleType[] = ['Explainer', 'Analysis', 'Roundup', 'State brief', 'Research digest', 'Opinion'];
export const PLURAL: Record<ArticleType, string> = {
  Explainer: 'Explainers', Analysis: 'Analysis', Roundup: 'Roundups', 'State brief': 'State briefs', 'Research digest': 'Research digests', Opinion: 'Opinion',
};
/** Article type marks, from the brand shapes, in a 12-unit box. */
export const TYPE_GLYPHS: Record<ArticleType, string> = {
  Explainer: '<circle cx="6" cy="6" r="5.5" fill="var(--red)"/>',
  Analysis: '<rect x=".5" y=".5" width="11" height="11" fill="var(--blue)"/>',
  Roundup: '<path d="M.5 11.5V.5a11 11 0 0 1 11 11z" fill="var(--sky)"/>',
  'State brief': '<path d="M6 0l6 6-6 6-6-6z" fill="var(--green)"/>',
  'Research digest': '<path d="M.5 7.5a5.5 5.5 0 0 1 11 0z" fill="var(--yellow)"/>',
  Opinion: '<path d="M6 .5L11.5 11.5H.5z" fill="var(--graphite)"/>',
};

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
/** '2026-10-18' -> '18 October 2026'. */
export const fullDate = (d: string) => { const [y, m, day] = d.split('-'); return `${+day} ${MONTHS[+m - 1]} ${y}`; };

export interface RecordNote {
  kind: 'record';
  id: string;
  jurisdiction: string;
  jurisdictionId: string;
  identifier: string | null;
  title: string;
  status: string;
  locator: string | null;
  quote: string | null;
  url: string;
  /** The record's entry on its state page. */
  page: string;
}
export interface StudyNote {
  kind: 'study';
  id: string;
  title: string;
  publisher: string;
  published: string;
  locator: string | null;
  quote: string | null;
  url: string;
}
export type Note = RecordNote | StudyNote;

// Provision quotes are not on PolicyRecord; read them from the same build.
let _quotes: Map<string, string> | undefined;
function provisionQuote(recordId: string, seq: number): string | null {
  if (!_quotes) {
    const dir = resolve(process.env.EDICTAI_BUILD_DIR ?? resolve(process.cwd(), '..', 'build'));
    _quotes = new Map(parseCsv(readFileSync(join(dir, 'csv', 'provisions.csv'), 'utf8')).map((r) => [`${r.record_id}#${r.seq}`, r.quote]));
  }
  return _quotes.get(`${recordId}#${seq}`) || null;
}

/** "Became law, 19 Mar 2024", "Did not pass, 2026", "Published 16 Sep 2026". */
export function statusLine(r: PolicyRecord): string {
  const f = r.fields;
  if (r.type === 'legislation') {
    const label = STATUS_LABELS[r.status] ?? r.status;
    if (r.status === 'enacted' && f.enacted_date) return `${label}, ${longDate(f.enacted_date)}`;
    if (r.status === 'failed' || r.status === 'vetoed') {
      const d = f.status_date ?? f.last_action_date;
      return d ? `${label}, ${d.slice(0, 4)}` : label;
    }
    const d = f.status_date ?? f.last_action_date;
    return d ? `${label}, ${longDate(d)}` : label;
  }
  if (r.type === 'executive-action') return f.signed ? `Signed ${longDate(f.signed)}` : STATUS_LABELS[r.status] ?? r.status;
  const d = f.updated ?? f.published;
  return d ? `${f.updated ? 'Updated' : 'Published'} ${longDate(d)}` : STATUS_LABELS[r.status] ?? r.status;
}

type Entry = CollectionEntry<'articles'>;
type Block = Entry['data']['body'][number];

export interface Article {
  slug: string;
  data: Entry['data'];
  type: ArticleType;
  notes: Note[];
  href: string;
  /** Jurisdiction ids of the records it cites (and the places its studies cover). */
  jurisdictions: string[];
  levels: string[];
  topics: string[];
  /** Fips codes of the states its records come from, for the map drawing. */
  fips: string[];
  drawing: 'map' | 'desk' | 'op';
  body: Block[];
}

let _articles: Article[] | undefined;
export async function articles(): Promise<Article[]> {
  if (_articles) return _articles;
  const recs = new Map(records().map((r) => [r.id, r]));
  const studies = new Map(research().map((s) => [s.id, s]));
  const juris = new Map(jurisdictions().map((j) => [j.id, j]));
  const entries = await getCollection('articles');
  _articles = entries.map((e) => {
    const slug = e.id.split('/').at(-1)!;
    const jset = new Set<string>(), lv = new Set<string>(), tp = new Set<string>();
    const notes: Note[] = e.data.notes.map((n) => {
      if ('record' in n) {
        const r = recs.get(n.record)!;
        const p = r.provisions[n.provision];
        jset.add(r.jurisdiction);
        r.educationLevels.forEach((l) => lv.add(l));
        r.topics.forEach((t) => tp.add(t));
        const j = juris.get(r.jurisdiction)!;
        return {
          kind: 'record', id: r.id, jurisdiction: r.jurisdiction === 'us' ? 'United States' : j.name, jurisdictionId: r.jurisdiction,
          identifier: r.identifier, title: r.title.replace(/\.$/, ''), status: statusLine(r),
          locator: p.locator, quote: provisionQuote(r.id, n.provision), url: p.sourceUrl ?? r.sources[0]?.url ?? url(r.path), page: url(r.path),
        } satisfies RecordNote;
      }
      const s = studies.get(n.study)!;
      const f = s.findings[n.finding];
      s.geography.forEach((g) => jset.add(g));
      s.educationLevels.forEach((l) => lv.add(l));
      s.topics.forEach((t) => tp.add(t));
      return { kind: 'study', id: s.id, title: s.title, publisher: s.publisher, published: s.published, locator: f.locator, quote: f.quote, url: f.sourceUrl } satisfies StudyNote;
    });
    const fips = [...new Set(notes.filter((n): n is RecordNote => n.kind === 'record').map((n) => juris.get(n.jurisdictionId)?.fips).filter(Boolean) as string[])];
    const type = e.data.type as ArticleType;
    return {
      slug, data: e.data, type, notes, href: url(`articles/${slug}/`),
      jurisdictions: [...jset], levels: [...lv], topics: [...tp], fips,
      drawing: fips.length ? 'map' : type === 'Opinion' ? 'op' : 'desk',
      body: e.data.body,
    } satisfies Article;
  });
  // Newest first; on the same day, in the order of the type tabs.
  _articles.sort((a, b) => b.data.published.localeCompare(a.data.published) || KINDS.indexOf(a.type) - KINDS.indexOf(b.type) || a.data.title.localeCompare(b.data.title));
  return _articles;
}

/** Filter values across all articles, in the order each dropdown lists them. */
export async function articleFacets() {
  const list = await articles();
  const juris = new Map(jurisdictions().map((j) => [j.id, j]));
  const tName = new Map(topics().map((t) => [t.code, t]));
  const used = (k: 'levels' | 'topics' | 'jurisdictions') => [...new Set(list.flatMap((a) => a[k]))];
  return {
    lv: ['k12', 'postsecondary'].filter((l) => used('levels').includes(l)).map((l) => [l, LEVELS[l]] as [string, string]),
    tp: used('topics').sort((a, b) => (tName.get(a)?.position ?? 0) - (tName.get(b)?.position ?? 0)).map((c) => [c, tName.get(c)?.name ?? c] as [string, string]),
    j: used('jurisdictions').map((id) => [id, id === 'us' ? 'Federal government' : juris.get(id)?.name ?? id] as [string, string])
      .sort((a, b) => (a[0] === 'us' ? -1 : b[0] === 'us' ? 1 : a[1].localeCompare(b[1]))),
  };
}

/** An article's notes as a dataset: the records and studies it cites, with locator, quote and link. */
export function notesDataset(a: Article): Dataset {
  const columns = ['note', 'kind', 'jurisdiction', 'identifier', 'title', 'status_or_publisher', 'locator', 'quote', 'source_url'] as const;
  const rows = a.notes.map((n, i): Row => n.kind === 'record'
    ? { note: String(i + 1), kind: 'record', jurisdiction: n.jurisdiction, identifier: n.identifier ?? '', title: n.title, status_or_publisher: n.status, locator: n.locator ?? '', quote: n.quote ?? '', source_url: n.url }
    : { note: String(i + 1), kind: 'study', jurisdiction: '', identifier: '', title: n.title, status_or_publisher: n.publisher, locator: n.locator ?? '', quote: n.quote ?? '', source_url: n.url });
  return { title: `Edictai article sources: ${a.data.title}`, columns, rows, meta: { article: a.slug } };
}
