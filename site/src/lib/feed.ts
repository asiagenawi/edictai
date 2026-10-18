/**
 * Atom feeds behind the Follow updates buttons: the newest and most recently changed records,
 * for the whole site or one jurisdiction. A feed is the one way to follow a static site without
 * an account. The follow/ page shows a feed in a browser and gives its address.
 */
import { url, type PolicyRecord } from './data';
import { STATUS_LABELS as STATUS } from './state-page';

const LIMIT = 100;
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);

/** When a record last changed: its latest action, else its own date, else when it was last verified. */
export function updatedOn(r: PolicyRecord): string {
  const last = r.actions.reduce((d, a) => (a.date > d ? a.date : d), '');
  return last || r.date || r.lastVerified || '1970-01-01';
}

const stamp = (d: string) => `${(d + '-01-01').slice(0, 10)}T00:00:00Z`;

export function atom(opts: { title: string; feedPath: string; pagePath: string; list: PolicyRecord[]; site: URL | undefined }): Response {
  const origin = (opts.site ?? new URL('https://asiagenawi.github.io')).origin;
  const abs = (p: string) => origin + url(p);
  const items = [...opts.list].sort((a, b) => updatedOn(b).localeCompare(updatedOn(a)) || a.id.localeCompare(b.id)).slice(0, LIMIT);
  const updated = items.length ? stamp(updatedOn(items[0])) : stamp('1970-01-01');
  const entries = items.map((r) => {
    const label = [r.stateCode === 'us' ? 'US' : r.stateCode.toUpperCase(), r.identifier].filter(Boolean).join(' ');
    const status = STATUS[r.status] ?? r.status;
    return `  <entry>
    <id>${esc(abs(r.path))}</id>
    <title>${esc(`${label}: ${r.title}`)}</title>
    <link href="${esc(abs(r.path))}"/>
    <updated>${stamp(updatedOn(r))}</updated>
    <category term="${esc(status)}"/>
    <summary>${esc(`${status}. ${r.summary}`)}</summary>
  </entry>`;
  });
  const xml = `<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>${esc(opts.title)}</title>
  <id>${esc(abs(opts.feedPath))}</id>
  <link rel="self" href="${esc(abs(opts.feedPath))}"/>
  <link href="${esc(abs(opts.pagePath))}"/>
  <updated>${updated}</updated>
  <author><name>Edictai</name></author>
${entries.join('\n')}
</feed>
`;
  return new Response(xml, { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
}
