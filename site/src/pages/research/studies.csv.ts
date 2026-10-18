/** Every research item as CSV: the file behind the Research page's Download CSV. */
import type { APIRoute } from 'astro';
import { research } from '../../lib/data';

const COLUMNS = [
  'id', 'classification', 'title', 'authors', 'publisher', 'published', 'education_levels', 'topics',
  'geography', 'summary', 'methods', 'key_findings', 'source_urls', 'archived_urls',
] as const;

const cell = (v: string) => (/[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

export const GET: APIRoute = () => {
  const rows = research().map((r): Record<(typeof COLUMNS)[number], string> => ({
    id: r.id,
    classification: r.classification,
    title: r.title,
    authors: r.authors.join('; '),
    publisher: r.publisher,
    published: r.published,
    education_levels: r.educationLevels.join(';'),
    topics: r.topics.join(';'),
    geography: r.geography.join(';'),
    summary: r.summary,
    methods: r.methods ?? '',
    key_findings: r.findings.map((f) => f.finding).join('\n'),
    source_urls: r.sources.map((s) => s.url).join(' '),
    archived_urls: r.sources.map((s) => s.archivedUrl).filter(Boolean).join(' '),
  }));
  const lines = [COLUMNS.join(','), ...rows.map((r) => COLUMNS.map((c) => cell(r[c])).join(','))];
  return new Response(lines.join('\r\n') + '\r\n', { headers: { 'Content-Type': 'text/csv; charset=utf-8' } });
};
