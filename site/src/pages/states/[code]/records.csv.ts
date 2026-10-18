/** One jurisdiction's records as CSV: the file behind the state page's Download CSV. */
import type { APIRoute } from 'astro';
import { jurisdictions, type State } from '../../../lib/data';
import { EXPORT_COLUMNS, exportRows } from '../../../lib/state-export';

export function getStaticPaths() {
  return jurisdictions().map((state) => ({ params: { code: state.code }, props: { state } }));
}

const cell = (v: string) => (/[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

export const GET: APIRoute = ({ props, site }) => {
  const rows = exportRows((props as { state: State }).state, site);
  const lines = [EXPORT_COLUMNS.join(','), ...rows.map((r) => EXPORT_COLUMNS.map((c) => cell(r[c])).join(','))];
  return new Response(lines.join('\r\n') + '\r\n', { headers: { 'Content-Type': 'text/csv; charset=utf-8' } });
};
