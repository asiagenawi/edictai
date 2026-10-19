/** An article's notes, in each download format: what the article's Download data menu saves. */
import type { APIRoute } from 'astro';
import { articles, notesDataset, type Article } from '../../../lib/articles';
import { FORMATS, respond, type Ext } from '../../../lib/dataset';

export async function getStaticPaths() {
  return (await articles()).flatMap((a) => FORMATS.map((f) => ({ params: { slug: a.slug, ext: f.ext }, props: { a } })));
}

export const GET: APIRoute = ({ params, props }) => respond(notesDataset((props as { a: Article }).a), params.ext as Ext);
