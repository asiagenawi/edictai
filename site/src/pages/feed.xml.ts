/** Every jurisdiction's records as an Atom feed: Follow updates on Bills & policies. */
import type { APIRoute } from 'astro';
import { records } from '../lib/data';
import { atom } from '../lib/feed';

export const GET: APIRoute = ({ site }) =>
  atom({ title: 'Edictai: Bills & policies', feedPath: 'feed.xml', pagePath: 'policies/', list: records(), site });
