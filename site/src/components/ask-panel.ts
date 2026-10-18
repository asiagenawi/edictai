/**
 * The Ask panel's behaviour: open and close, the conversation, and talking to
 * the Ask API's stream (POST /api/ask/stream, server-sent events).
 *
 * The conversation is kept in sessionStorage, so it follows the visitor from
 * page to page in this tab and is gone when the tab closes. It is never sent
 * anywhere except as the last few messages of context with the next question.
 */
import rough from 'roughjs';

type Rec = {
  id: string; type: string; classification?: string | null; identifier?: string | null; title: string;
  jurisdiction?: string | null; jurisdiction_name?: string | null; status?: string | null; publisher?: string | null;
  verification?: string | null;
};
type Answer = {
  kind: 'conversation' | 'records';
  records_layer: { text: string; citations: string[] };
  table: string[] | null;
  general_knowledge: { text: string; label: string } | null;
  records: Record<string, Rec>;
  no_match: boolean;
  follow_ups: string[];
  data_as_of: string | null;
};
type Turn = { q: string; a?: Answer; err?: string };

const STORE = 'edictai-ask';
const OPEN = 'edictai-ask-open';
const HISTORY_MESSAGES = 6;
const WAKE_AFTER_MS = 4500;
const GIVE_UP_MS = 150_000;
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const EXAMPLES: [string, string][] = [
  ['Which states', 'Which states require school districts to adopt an AI policy?'],
  ['A specific bill', 'What does California’s AB 1159 do?'],
  ['One state', 'Has Wyoming passed a law on AI in schools?'],
];
const MSG = {
  offline: 'Ask isn’t reachable right now. Check your connection, or try again in a minute.',
  down: 'Ask isn’t available right now. Please try again in a few minutes.',
  slow: 'That took too long to answer. Please try again.',
  rate: 'You’ve asked a lot in a short time. Give it a minute, then ask again.',
  broken: 'Something went wrong on my end. Please try asking again.',
};
// Brand shapes for record chips, in a 10-unit box.
const GL = {
  bill: '<circle cx="5" cy="5" r="4.5" fill="var(--red)"/>',
  order: '<path d="M5 .5 9.5 5 5 9.5.5 5z" fill="var(--green)"/>',
  guide: '<path d="M.5 9.5V.5a9 9 0 0 1 9 9z" fill="var(--sky)"/>',
  rule: '<path d="M.5 7.5a4.5 4.5 0 0 1 9 0z" fill="var(--yellow)" transform="translate(0 -1.5)"/>',
  study: '<path d="M.5 7.5a4.5 4.5 0 0 1 9 0z" fill="var(--yellow)" transform="translate(0 -1.5)"/>',
};

const esc = (s: unknown) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
const longDate = (d: string | null) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d || '');
  return m ? `${+m[3]} ${MONTHS[+m[2] - 1]} ${m[1]}` : d || '';
};

export function initAsk() {
  const P = document.getElementById('askp');
  if (!P || P.dataset.ready) return;
  P.dataset.ready = '1';
  const API = P.dataset.api!;
  const BASE = P.dataset.base!;
  const JUR = P.dataset.jurisdiction || '';
  const btn = document.getElementById('ask-open') as HTMLButtonElement | null;
  const body = P.querySelector<HTMLElement>('.ap-body')!;
  const form = P.querySelector<HTMLFormElement>('.ap-foot')!;
  const box = P.querySelector<HTMLTextAreaElement>('#ap-q')!;
  const send = P.querySelector<HTMLButtonElement>('.ap-send')!;
  const MARK = P.querySelector('.ap-mark svg')!.outerHTML;
  const html = document.documentElement;

  let turns: Turn[] = [];
  try { turns = JSON.parse(sessionStorage.getItem(STORE) || '[]'); } catch { turns = []; }
  if (!Array.isArray(turns)) turns = [];
  const save = () => { try { sessionStorage.setItem(STORE, JSON.stringify(turns.slice(-20))); } catch { /* storage off */ } };

  ribbon(P.querySelector('.ap-rib')!);

  // ---- open / close --------------------------------------------------------
  let warmed = false;
  function warm() {
    // The free host sleeps when idle; a cheap request starts it waking while the visitor types.
    if (warmed) return;
    warmed = true;
    fetch(`${API}/api/health`, { mode: 'cors', cache: 'no-store' }).catch(() => {});
  }
  function setOpen(v: boolean, focus = true) {
    html.classList.remove('ask-instant');
    html.classList.toggle('ask-open', v);
    P!.setAttribute('aria-hidden', String(!v));
    (P as HTMLElement & { inert: boolean }).inert = !v;
    btn?.setAttribute('aria-expanded', String(v));
    try { sessionStorage.setItem(OPEN, v ? '1' : '0'); } catch { /* storage off */ }
    // Pages that size themselves to the window (the States map) lay out again.
    dispatchEvent(new Event('resize'));
    setTimeout(() => dispatchEvent(new Event('resize')), 300);
    if (v) {
      warm();
      if (focus) setTimeout(() => box.focus({ preventScroll: true }), 280);
    } else if (focus && P!.contains(document.activeElement)) btn?.focus();
  }
  const isOpen = () => html.classList.contains('ask-open');

  if (!btn) {
    // A page without the Ask button (home, About) keeps the panel closed.
    html.classList.remove('ask-open', 'ask-instant');
    (P as HTMLElement & { inert: boolean }).inert = true;
  } else {
    btn.addEventListener('click', () => setOpen(!isOpen()));
    if (isOpen()) {
      setOpen(true, false);
      html.classList.add('ask-instant');
      requestAnimationFrame(() => requestAnimationFrame(() => html.classList.remove('ask-instant')));
    } else (P as HTMLElement & { inert: boolean }).inert = true;
  }
  P.querySelector('.ap-x')!.addEventListener('click', () => setOpen(false));
  addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isOpen() && !document.querySelector('dialog[open]')) setOpen(false);
  });

  // ---- drawing the conversation ---------------------------------------------
  function start() {
    body.innerHTML = `<div class="ap-start"><svg class="ap-draw" viewBox="0 0 240 120" aria-hidden="true"></svg>`
      + `<h3>What are you looking into?</h3><p>Ask about a state, a bill, or a kind of rule. I’ll answer from Edictai’s records and link every source.</p>`
      + `<ul>${EXAMPLES.map(([k, q]) => `<li><button type="button" data-q="${esc(q)}"><span class="k">${esc(k)}</span><span class="t">${esc(q)}</span><span class="a" aria-hidden="true">→</span></button></li>`).join('')}</ul></div>`;
    drawing(body.querySelector('.ap-draw')!);
  }

  function chip(id: string, recs: Record<string, Rec>) {
    const r = recs[id];
    if (!r) return '';
    let glyph = GL.rule, href: string, lead: string, label: string;
    if (r.type === 'research') {
      glyph = GL.study;
      href = `${BASE}research/#${encodeURIComponent(r.id)}`;
      lead = 'Study';
      label = (r.publisher || r.title).split(' (')[0];
    } else {
      glyph = r.type === 'legislation' ? GL.bill : r.type === 'executive-action' ? GL.order
        : /guid|framework|report|toolkit|statement/.test(r.classification || '') ? GL.guide : GL.rule;
      const j = r.jurisdiction || 'us';
      const code = j === 'us' ? 'us' : j.replace(/^us-/, '');
      href = `${BASE}states/${code}/#${encodeURIComponent(r.id)}`;
      lead = code.toUpperCase();
      label = r.identifier || r.title;
    }
    if (label.length > 30) label = label.slice(0, 28).trimEnd() + '…';
    const tip = [r.title, r.status, r.verification === 'unverified' ? 'not yet verified' : ''].filter(Boolean).join(' · ');
    return `<a class="ap-chip" href="${esc(href)}" title="${esc(tip)}"><svg viewBox="0 0 10 10" aria-hidden="true">${glyph}</svg><b>${esc(lead)}</b>${esc(label)}</a>`;
  }

  function md(text: string, recs: Record<string, Rec>) {
    // A record cited again later in the same answer keeps only its first chip.
    const seen = new Set<string>();
    const cite = (id: string) => (seen.has(id) ? '' : (seen.add(id), chip(id, recs)));
    const inline = (s: string) => esc(s)
      .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
      .replace(/(^|[^*\w])\*([^*\n]+?)\*(?!\w)/g, '$1<i>$2</i>')
      .replace(/\[\[([^\]\s]+)\]\]/g, (_, id: string) => cite(id.trim()))
      .replace(/ +([.,;:])/g, '$1');
    let h = '';
    for (const block of text.trim().split(/\n{2,}/)) {
      const lines = block.split('\n').filter((l) => l.trim());
      if (!lines.length) continue;
      const bullet = /^\s*([-*•]|\d+[.)])\s+/;
      if (lines.every((l) => bullet.test(l))) {
        const tag = /^\s*\d/.test(lines[0]) ? 'ol' : 'ul';
        h += `<${tag}>${lines.map((l) => `<li>${inline(l.replace(bullet, ''))}</li>`).join('')}</${tag}>`;
      } else if (lines.length > 1 && bullet.test(lines[1]) && lines.slice(1).every((l) => bullet.test(l))) {
        h += `<p>${inline(lines[0])}</p><ul>${lines.slice(1).map((l) => `<li>${inline(l.replace(bullet, ''))}</li>`).join('')}</ul>`;
      } else {
        h += `<p>${lines.map(inline).join('<br>')}</p>`;
      }
    }
    return h;
  }

  function answerHtml(a: Answer) {
    const recs = a.records || {};
    let h = `<div class="ap-ans">${md(a.records_layer.text, recs)}`;
    if (a.table?.length) {
      h += `<table class="ap-t2"><thead><tr><th>Where</th><th>Record</th><th>Status</th></tr></thead><tbody>${a.table.map((id) => {
        const r = recs[id];
        return r ? `<tr><td>${esc(r.jurisdiction === 'us' ? 'Federal' : r.jurisdiction_name || '')}</td><td>${chip(id, recs)}</td><td>${esc(r.status || '')}</td></tr>` : '';
      }).join('')}</tbody></table>`;
    }
    h += '</div>';
    if (a.general_knowledge) {
      h += `<div class="ap-ans ap-out"><h3>${esc(a.general_knowledge.label)}</h3>${md(a.general_knowledge.text, {})}</div>`;
    }
    if (a.records_layer.citations.length && a.data_as_of) h += `<p class="ap-stamp">Edictai data as of ${esc(longDate(a.data_as_of))}</p>`;
    if (a.follow_ups?.length) h += `<div class="ap-next">${a.follow_ups.map((f) => `<button type="button" data-q="${esc(f)}">${esc(f)}</button>`).join('')}</div>`;
    return h;
  }

  function turnEl(t: Turn) {
    const el = document.createElement('section');
    el.className = 'ap-turn';
    el.innerHTML = `<p class="ap-qq">${esc(t.q)}</p><div class="ap-who">${MARK}<span>Edictai</span></div><div class="ap-out-slot"></div>`;
    fill(el, t);
    return el;
  }
  function fill(el: HTMLElement, t: Turn) {
    const slot = el.querySelector<HTMLElement>('.ap-out-slot')!;
    if (t.a) slot.innerHTML = answerHtml(t.a);
    else if (t.err) slot.innerHTML = `<p class="ap-err">${esc(t.err)} <button type="button" data-retry>Try again</button></p>`;
  }

  function render() {
    body.innerHTML = '';
    if (!turns.length) return start();
    for (const t of turns) body.append(turnEl(t));
    body.scrollTop = body.scrollHeight;
  }

  // ---- asking ---------------------------------------------------------------
  let busy = false;
  let ctl: AbortController | null = null;

  function context() {
    const msgs: { role: string; content: string }[] = [];
    for (const t of turns) {
      if (!t.a) continue;
      msgs.push({ role: 'user', content: t.q }, { role: 'assistant', content: t.a.records_layer.text });
    }
    return msgs.slice(-HISTORY_MESSAGES);
  }

  async function ask(q: string) {
    q = q.trim();
    if (!q || busy) return;
    busy = true;
    send.disabled = true;
    if (!turns.length) body.innerHTML = '';
    const t: Turn = { q };
    const hist = context();
    turns.push(t);
    const el = turnEl(t);
    const slot = el.querySelector<HTMLElement>('.ap-out-slot')!;
    slot.innerHTML = '<p class="ap-look"><i></i><i></i><i></i><span>Thinking…</span></p>';
    const look = slot.querySelector('span')!;
    body.append(el);
    el.scrollIntoView({ block: 'start', behavior: 'smooth' });
    try {
      t.a = await stream(q, hist, (s) => { look.textContent = s; });
    } catch (e) {
      t.err = e instanceof Error && e.message ? e.message : MSG.broken;
    } finally {
      busy = false;
      send.disabled = false;
      ctl = null;
    }
    if (!turns.includes(t)) return; // a new conversation started meanwhile
    fill(el, t);
    save();
    el.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }

  async function stream(q: string, hist: unknown[], status: (s: string) => void): Promise<Answer> {
    ctl = new AbortController();
    const signal = ctl.signal;
    const giveUp = setTimeout(() => ctl?.abort('slow'), GIVE_UP_MS);
    let awake = false;
    const wake = setTimeout(() => { if (!awake) status('Waking up Ask. After a quiet spell this takes about half a minute…'); }, WAKE_AFTER_MS);
    const payload = JSON.stringify({ question: q, history: hist, ...(JUR ? { scope: { jurisdiction: JUR } } : {}) });
    try {
      let res: Response | null = null;
      // A sleeping host can answer with a gateway error while it starts; wait for it and try once more.
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          res = await fetch(`${API}/api/ask/stream`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: payload, signal });
        } catch (e) {
          if (signal.aborted) throw e;
          res = null;
        }
        if (res && ![502, 503, 504].includes(res.status)) break;
        if (attempt === 0) {
          status('Waking up Ask. After a quiet spell this takes about half a minute…');
          if (!(await waitForHealth(signal))) break;
        }
      }
      if (!res) throw new Error(MSG.offline);
      if (!res.ok) {
        const data = await res.json().catch(() => ({} as Record<string, unknown>));
        if (res.status === 429) throw new Error(data.budget_exhausted ? String(data.error) : MSG.rate);
        if (res.status === 400 && data.error) throw new Error(String(data.error));
        throw new Error(MSG.down);
      }
      awake = true;
      const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
      let buf = '', answer: Answer | null = null, error = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += value;
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, i);
          buf = buf.slice(i + 2);
          for (const line of frame.split('\n')) {
            if (!line.startsWith('data:')) continue;
            let ev: Record<string, unknown>;
            try { ev = JSON.parse(line.slice(5)); } catch { continue; }
            if (ev.started) status('Thinking…');
            else if (typeof ev.progress === 'string') {
              status(/^Searching Edictai records/.test(ev.progress) ? 'Looking through the records…' : ev.progress);
            }
            else if (ev.answer) answer = ev.answer as Answer;
            else if (typeof ev.error === 'string') error = ev.error;
          }
        }
      }
      if (answer) return answer;
      throw new Error(error ? MSG.down : MSG.broken);
    } catch (e) {
      if (signal.aborted) throw new Error(signal.reason === 'slow' ? MSG.slow : '');
      throw e;
    } finally {
      clearTimeout(giveUp);
      clearTimeout(wake);
    }
  }

  async function waitForHealth(signal: AbortSignal) {
    const until = Date.now() + 75_000;
    while (Date.now() < until && !signal.aborted) {
      try {
        const r = await fetch(`${API}/api/health`, { cache: 'no-store', signal });
        if (r.ok) return true;
      } catch { /* still asleep */ }
      await new Promise((r) => setTimeout(r, 3000));
    }
    return false;
  }

  // ---- events ---------------------------------------------------------------
  body.addEventListener('click', (e) => {
    const target = e.target as HTMLElement;
    const q = target.closest<HTMLElement>('[data-q]');
    if (q) return void ask(q.dataset.q!);
    if (target.closest('[data-retry]')) {
      const i = turns.length - 1;
      const last = turns[i];
      if (last?.err) { turns.splice(i, 1); target.closest('.ap-turn')?.remove(); ask(last.q); }
      return;
    }
    // A chip for a record on this very page opens it in place.
    const a = target.closest<HTMLAnchorElement>('a.ap-chip');
    if (a && a.pathname === location.pathname && a.hash) {
      const row = document.getElementById(decodeURIComponent(a.hash.slice(1)));
      if (row?.classList.contains('reg-row')) {
        e.preventDefault();
        try { history.replaceState(null, '', a.hash); } catch { /* ignore */ }
        if (row.getAttribute('aria-expanded') !== 'true') row.click();
        row.scrollIntoView({ block: 'start', behavior: 'smooth' });
        if (!matchMedia('(min-width:1000px)').matches) setOpen(false, false);
      }
    }
  });

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const v = box.value;
    if (!v.trim() || busy) return;
    box.value = '';
    grow();
    ask(v);
  });
  box.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
  });
  const grow = () => { box.style.height = 'auto'; box.style.height = Math.min(box.scrollHeight, 140) + 'px'; };
  box.addEventListener('input', grow);

  P.querySelector('.ap-new')!.addEventListener('click', () => {
    ctl?.abort();
    turns = [];
    save();
    start();
    box.focus();
  });
  // Report a problem: the shared reader form, with the last question filled in.
  P.querySelector('.ap-report')!.addEventListener('click', () => {
    const f = document.querySelector<HTMLInputElement>('#askfix input[name="question"]');
    const last = turns.at(-1);
    if (f && last && !f.value) f.value = last.q;
  });

  render();
}

/** A thin band of brand shapes across the top of the panel. */
function ribbon(el: HTMLElement) {
  const P = ['--red', '--yellow', '--blue', '--sky', '--green', '--mint', '--pink', '--graphite'];
  let o = '', x = 0, i = 7;
  const r = () => (i = (i * 9301 + 49297) % 233280) / 233280;
  while (x < 460) {
    const c = `var(${P[Math.floor(r() * P.length)]})`, k = Math.floor(r() * 5);
    o += k === 0 ? `<circle cx="${x + 4.5}" cy="6" r="4" fill="${c}"/>`
      : k === 1 ? `<rect x="${x + 0.5}" y="1.5" width="8" height="8" fill="${c}"/>`
      : k === 2 ? `<path d="M${x + 4.5} 1l4.5 5-4.5 5-4.5-5z" fill="${c}"/>`
      : k === 3 ? `<path d="M${x} 10.5a4.5 4.5 0 0 1 9 0z" fill="${c}"/>`
      : `<path d="M${x + 4.5} 1.5l4.5 8.5H${x}z" fill="${c}"/>`;
    x += 12 + Math.floor(r() * 4);
  }
  el.innerHTML = `<svg viewBox="0 0 460 12" preserveAspectRatio="xMinYMid slice" width="100%" height="12">${o}</svg>`;
}

/** The empty state's hand-drawn speech bubble, shapes and pencil. */
function drawing(svg: SVGSVGElement) {
  const rc = rough.svg(svg);
  const C = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  let sd = 11;
  const o = (x: Record<string, unknown> = {}) => ({ seed: sd++, roughness: 1.2, bowing: 1.3, stroke: '#2b2a28', strokeWidth: 1.1, ...x });
  const bub = 'M40 20q0-10 10-10h120q10 0 10 10v46q0 10-10 10H92l-22 18 4-18H50q-10 0-10-10z';
  svg.append(rc.path(bub, o({ stroke: 'none', fill: C('--sky'), fillStyle: 'hachure', hachureGap: 3.2, hachureAngle: -50, fillWeight: 1 })));
  svg.append(rc.path(bub, o()));
  svg.append(rc.circle(66, 42, 13, o({ fill: C('--red'), fillStyle: 'hachure', hachureGap: 2, stroke: 'none' })));
  svg.append(rc.rectangle(84, 35, 13, 13, o({ fill: C('--blue'), fillStyle: 'hachure', hachureGap: 2, stroke: 'none' })));
  svg.append(rc.path('M110 48l7-13 7 13z', o({ fill: C('--green'), fillStyle: 'hachure', hachureGap: 2, stroke: 'none' })));
  const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  t.setAttribute('x', '134'); t.setAttribute('y', '50');
  t.setAttribute('font-family', "Caveat, 'Bradley Hand', cursive"); t.setAttribute('font-size', '22'); t.setAttribute('fill', '#2b2a28');
  t.textContent = '?';
  svg.append(t);
  svg.append(rc.path('M186 100l38-30 5 6-38 30z', o({ fill: C('--yellow'), fillStyle: 'hachure', hachureGap: 1.8, hachureAngle: 30 })));
  svg.append(rc.path('M186 100l-8 8 11-3', o()));
}
