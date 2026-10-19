/** The record datasets behind the Download menus: a jurisdiction's records, or every record. */
import { provisionTypes, recordsFor, url, type PolicyRecord, type State } from './data';
import type { Dataset, Row } from './dataset';

export const RECORD_COLUMNS = [
  'id', 'record_type', 'classification', 'identifier', 'title', 'status', 'date', 'issuer',
  'education_levels', 'topics', 'provision_types', 'summary', 'page_url',
] as const;

export function recordRows(list: PolicyRecord[], site: URL | undefined): Row[] {
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
    education_levels: r.educationLevels,
    topics: r.topics,
    provision_types: [...new Set(r.provisions.map((p) => p.type).filter((t) => known.has(t)))],
    summary: r.summary,
    page_url: site ? new URL(url(r.path), site).href : url(r.path),
  }));
}

export function recordsDataset(list: PolicyRecord[], site: URL | undefined, title: string, meta?: Record<string, string>): Dataset {
  return { title, columns: RECORD_COLUMNS, rows: recordRows(list, site), meta };
}

export function stateDataset(state: State, site: URL | undefined): Dataset {
  return recordsDataset(recordsFor(state.id), site, `Edictai records: ${state.name}`, { jurisdiction: state.id, name: state.name });
}
