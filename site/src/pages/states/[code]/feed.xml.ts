/** One jurisdiction's records as an Atom feed: Follow updates on the state page. */
import type { APIRoute } from 'astro';
import { jurisdictions, recordsFor, type State } from '../../../lib/data';
import { atom } from '../../../lib/feed';

export function getStaticPaths() {
  return jurisdictions().map((state) => ({ params: { code: state.code }, props: { state } }));
}

export const GET: APIRoute = ({ props, site }) => {
  const { state } = props as { state: State };
  return atom({ title: `Edictai: ${state.name}`, feedPath: `states/${state.code}/feed.xml`, pagePath: `states/${state.code}/`, list: recordsFor(state.id), site });
};
