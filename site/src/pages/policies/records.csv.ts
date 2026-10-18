/** Every record as CSV: the file behind the Bills & policies page's Download CSV. */
import type { APIRoute } from 'astro';
import { records } from '../../lib/data';
import { EXPORT_COLUMNS, exportRecords } from '../../lib/state-export';

const cell = (v: string) => (/[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

export const GET: APIRoute = ({ site }) => {
  const rows = exportRecords(records(), site);
  const lines = [EXPORT_COLUMNS.join(','), ...rows.map((r) => EXPORT_COLUMNS.map((c) => cell(r[c])).join(','))];
  return new Response(lines.join('\r\n') + '\r\n', { headers: { 'Content-Type': 'text/csv; charset=utf-8' } });
};
