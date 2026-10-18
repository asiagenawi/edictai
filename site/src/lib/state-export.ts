/** Flat per-record rows for the CSV and JSON downloads (a jurisdiction's, or every record's). */
import { provisionTypes, recordsFor, url, type PolicyRecord, type State } from './data';

export const EXPORT_COLUMNS = [
  'id', 'record_type', 'classification', 'identifier', 'title', 'status', 'date', 'issuer',
  'education_levels', 'topics', 'provision_types', 'summary', 'page_url',
] as const;
export type ExportRow = Record<(typeof EXPORT_COLUMNS)[number], string>;

export function exportRows(state: State, site: URL | undefined): ExportRow[] {
  return exportRecords(recordsFor(state.id), site);
}

/** The same rows for any list of records (Bills & policies exports every record). */
export function exportRecords(list: PolicyRecord[], site: URL | undefined): ExportRow[] {
  const known = new Set(provisionTypes().map((p) => p.code));
  return list.map((r) => ({
    id: r.id,
    record_type: r.type,
    classification: r.classification,
    identifier: r.identifier ?? '',
    title: r.title,
    status: r.status,
    date: r.date ?? '',
    issuer: r.issuer ?? '',
    education_levels: r.educationLevels.join(';'),
    topics: r.topics.join(';'),
    provision_types: [...new Set(r.provisions.map((p) => p.type).filter((t) => known.has(t)))].join(';'),
    summary: r.summary,
    page_url: site ? new URL(url(r.path), site).href : url(r.path),
  }));
}
