/** An article's notes as CSV: the records and studies it cites, with locator, quote and link. */
import type { APIRoute } from 'astro';
import { articles, notesCsv, type Article } from '../../../lib/articles';

export async function getStaticPaths() {
  return (await articles()).map((a) => ({ params: { slug: a.slug }, props: { a } }));
}

export const GET: APIRoute = ({ props }) =>
  new Response(notesCsv((props as { a: Article }).a), { headers: { 'Content-Type': 'text/csv; charset=utf-8' } });
