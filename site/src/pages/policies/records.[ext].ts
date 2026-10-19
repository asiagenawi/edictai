/** Every record, in each download format: the files behind the Bills & policies page's Download menu. */
import type { APIRoute } from 'astro';
import { records } from '../../lib/data';
import { FORMATS, respond, type Ext } from '../../lib/dataset';
import { recordsDataset } from '../../lib/state-export';

export const getStaticPaths = () => FORMATS.map((f) => ({ params: { ext: f.ext } }));

export const GET: APIRoute = ({ params, site }) =>
  respond(recordsDataset(records(), site, 'Edictai records'), params.ext as Ext);
