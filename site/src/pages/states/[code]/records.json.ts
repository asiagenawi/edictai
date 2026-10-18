/** One jurisdiction's records as JSON, with the same fields as the CSV. */
import type { APIRoute } from 'astro';
import { jurisdictions, type State } from '../../../lib/data';
import { exportRows } from '../../../lib/state-export';

export function getStaticPaths() {
  return jurisdictions().map((state) => ({ params: { code: state.code }, props: { state } }));
}

export const GET: APIRoute = ({ props, site }) => {
  const { state } = props as { state: State };
  const body = { jurisdiction: state.id, name: state.name, records: exportRows(state, site) };
  return new Response(JSON.stringify(body, null, 2) + '\n', { headers: { 'Content-Type': 'application/json' } });
};
