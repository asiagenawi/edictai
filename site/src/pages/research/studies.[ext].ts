/** Every research item, in each download format: the files behind the Research page's Download menu. */
import type { APIRoute } from 'astro';
import { FORMATS, respond, type Ext } from '../../lib/dataset';
import { researchDataset } from '../../lib/research-export';

export const getStaticPaths = () => FORMATS.map((f) => ({ params: { ext: f.ext } }));

export const GET: APIRoute = ({ params }) => respond(researchDataset(), params.ext as Ext);
