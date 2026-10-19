/** The research dataset behind the Research page's Download menu. */
import { research } from './data';
import type { Dataset, Row } from './dataset';

const COLUMNS = [
  'id', 'classification', 'title', 'authors', 'publisher', 'published', 'education_levels', 'topics',
  'geography', 'summary', 'methods', 'key_findings', 'source_urls', 'archived_urls',
] as const;

export const RESEARCH_SEPS = { authors: '; ', key_findings: '\n', source_urls: ' ', archived_urls: ' ' };

export function researchDataset(): Dataset {
  const rows = research().map((r): Row => ({
    id: r.id,
    classification: r.classification,
    title: r.title,
    authors: r.authors,
    publisher: r.publisher,
    published: r.published,
    education_levels: r.educationLevels,
    topics: r.topics,
    geography: r.geography,
    summary: r.summary,
    methods: r.methods ?? '',
    key_findings: r.findings.map((f) => f.finding),
    source_urls: r.sources.map((s) => s.url),
    archived_urls: r.sources.map((s) => s.archivedUrl).filter((u): u is string => Boolean(u)),
  }));
  return { title: 'Edictai research', columns: COLUMNS, rows, seps: RESEARCH_SEPS };
}
