/**
 * Reader forms (corrections, study suggestions, messages, pitches) are delivered by FormSubmit,
 * which emails each submission to the editors; the site is static and has no server of its own.
 * The endpoint uses FormSubmit's alias for the editors' address, so the address is not in the page.
 */
const ENDPOINT = 'https://formsubmit.co/ajax/b1bccfcbf750634b4c127e5f68220bb2';

/** Send one submission. Resolves when FormSubmit accepts it; rejects otherwise. */
export async function send(subject: string, fields: Record<string, string>, replyTo?: string): Promise<void> {
  const res = await fetch(ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ _subject: subject, _template: 'table', _captcha: 'false', ...(replyTo ? { _replyto: replyTo } : {}), ...fields }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || String(data.success) !== 'true') throw new Error(data.message || `HTTP ${res.status}`);
}
