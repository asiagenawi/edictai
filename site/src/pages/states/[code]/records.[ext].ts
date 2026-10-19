/** One jurisdiction's records, in each download format: the files behind the state page's Download menu. */
import type { APIRoute } from 'astro';
import { jurisdictions, type State } from '../../../lib/data';
import { FORMATS, respond, type Ext } from '../../../lib/dataset';
import { stateDataset } from '../../../lib/state-export';

export function getStaticPaths() {
  return jurisdictions().flatMap((state) => FORMATS.map((f) => ({ params: { code: state.code, ext: f.ext }, props: { state } })));
}

export const GET: APIRoute = ({ params, props, site }) =>
  respond(stateDataset((props as { state: State }).state, site), params.ext as Ext);
