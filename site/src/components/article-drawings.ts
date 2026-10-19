/**
 * Hand-drawn illustrations for the Articles index. rough.js gives every line a slight wobble and
 * fills colour as loose marker hatching, laid a hair off the ink outline the way a quick sketch
 * is. Each drawing is seeded by its article's slug, so it is the same on every visit.
 *
 * Articles citing state records: a pencil map with those states hatched in brand colours.
 * Research digests: a desk with a chalkboard, a laptop and two arrows. Federal pieces: the
 * Capitol. State law briefs: a signed bill and a gavel. Teaching: a classroom. Deepfakes: a
 * phone. Roundups: a calendar page. Student data: a padlock on a folder. The Opinion
 * invitation: a speech bubble and a megaphone.
 */
import rough from 'roughjs';

type MapState = { fips: string; d: string };
const INK = '#2b2a28', PAPER = '#fbf8f1';
const NS = 'http://www.w3.org/2000/svg';
const CV = (n: string) => (n === '--white' ? '#ffffff' : getComputedStyle(document.documentElement).getPropertyValue(n).trim());

function hseed(s: string) { let x = 7; for (const c of s) x = (x * 33 + c.charCodeAt(0)) % 2147483647; return x || 1; }

/** Warm paper with a faint grain. */
function paper(svg: SVGSVGElement, seed: number) {
  const f = `grain${seed}`;
  svg.innerHTML = `<defs><filter id="${f}" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" seed="${seed % 97}"/><feColorMatrix values="0 0 0 0 .55  0 0 0 0 .5  0 0 0 0 .42  0 0 0 .07 0"/></filter></defs><rect width="240" height="200" fill="${PAPER}"/><rect width="240" height="200" filter="url(#${f})"/>`;
}

interface O { c?: string; w?: number; r?: number; b?: number; s?: number; gap?: number; angle?: number; dx?: number; dy?: number; style?: string; rot?: number }
function painter(svg: SVGSVGElement, seed: number) {
  const rc = rough.svg(svg);
  let n = 0;
  const S = () => seed + (++n) * 101;
  const add = <T extends Element>(el: T) => { svg.appendChild(el); return el; };
  const off = (el: Element, dx: number, dy: number) => { el.setAttribute('transform', `translate(${dx} ${dy})`); return el; };
  return {
    // colour: marker hatching, nudged off the line
    fill: (d: string, c: string, o: O = {}) => off(add(rc.path(d, { seed: S(), stroke: 'none', fill: CV(c), fillStyle: o.style || 'hachure', hachureGap: o.gap || 2.6, hachureAngle: o.angle ?? -38, fillWeight: o.w || 1.1, roughness: o.r ?? 1.3 })), o.dx ?? 1.2, o.dy ?? 1),
    // ink: thin, slightly bowed line
    ink: (d: string, o: O = {}) => add(rc.path(d, { seed: S(), stroke: o.c || INK, strokeWidth: o.w || 1, roughness: o.r ?? 1.1, bowing: o.b ?? 1.4, fill: 'none' })),
    line: (x1: number, y1: number, x2: number, y2: number, o: O = {}) => add(rc.line(x1, y1, x2, y2, { seed: S(), stroke: o.c || INK, strokeWidth: o.w || 1, roughness: o.r ?? 1.2, bowing: o.b ?? 1.6 })),
    arc: (x: number, y: number, w: number, h: number, a0: number, a1: number, o: O = {}) => add(rc.arc(x, y, w, h, a0, a1, false, { seed: S(), stroke: o.c || INK, strokeWidth: o.w || 1.2, roughness: o.r ?? 1.2 })),
    circ: (x: number, y: number, d: number, o: O = {}) => add(rc.circle(x, y, d, { seed: S(), stroke: o.c || INK, strokeWidth: o.w || 1.1, roughness: o.r ?? 1.1, fill: 'none' })),
    text: (x: number, y: number, t: string, o: O = {}) => {
      const e = document.createElementNS(NS, 'text');
      e.setAttribute('x', String(x)); e.setAttribute('y', String(y));
      e.setAttribute('font-family', "Caveat, 'Bradley Hand', cursive"); e.setAttribute('font-weight', '500');
      e.setAttribute('font-size', String(o.s || 14)); e.setAttribute('fill', o.c || INK);
      if (o.rot) e.setAttribute('transform', `rotate(${o.rot} ${x} ${y})`);
      e.textContent = t;
      return add(e);
    },
  };
}
const R = (x: number, y: number, w: number, h: number) => `M${x} ${y}h${w}v${h}h${-w}z`;

/** The cited states, hatched in marker over a pencil map of the country; a scrawled note and tick in the corner. */
function paintMap(svg: SVGSVGElement, slug: string, on: Set<string>, states: MapState[], note: string) {
  const seed = hseed(slug);
  paper(svg, seed);
  const P = painter(svg, seed);
  const pal = ['--red', '--blue', '--green', '--yellow', '--sky', '--pink', '--mint'];
  states.forEach((s, i) => { if (on.has(s.fips)) P.fill(s.d, pal[i % pal.length], { angle: -30 - (i % 5) * 14, gap: 2.1 }); });
  states.forEach((s) => P.ink(s.d, { w: on.has(s.fips) ? 0.9 : 0.45, r: 0.55, b: 0.6, c: on.has(s.fips) ? INK : '#8c877d' }));
  if (note) { P.text(150, 170, note, { s: 13, rot: -4 }); P.line(150, 175, 214, 170, { w: 0.8 }); }
  P.ink('M206 150l4 6 9-13', { w: 1.6, c: CV('--red') });
}

/** A chalkboard with a sum on it, a laptop with a tutor's reply, a pencil, and two results as arrows. */
function paintDesk(svg: SVGSVGElement, slug: string) {
  const seed = hseed(slug);
  paper(svg, seed);
  const P = painter(svg, seed);
  P.fill(R(24, 22, 132, 78), '--green', { gap: 2, w: 1.6, angle: -62, dx: 0, dy: 0 }); P.ink(R(24, 22, 132, 78), { w: 1.4 });
  P.text(36, 54, '3 × 4 = ?', { s: 24, c: '#fff', rot: -2 }); P.text(44, 86, 'try again :)', { s: 18, c: '#fff6d8', rot: -3 });
  P.ink(R(84, 108, 94, 56), { w: 1.2 }); P.ink(R(90, 114, 82, 44), { w: 0.8 }); P.fill(R(90, 114, 82, 44), '--sky', { gap: 3, angle: -50 });
  P.ink('M74 170h114l-8-6H82z', { w: 1.1 });
  const b1 = 'M98 122h40a5 5 0 0 1 0 10h-40a5 5 0 0 1 0-10z', b2 = 'M124 138h36a5 5 0 0 1 0 10h-36a5 5 0 0 1 0-10z';
  P.ink(b1, { w: 0.8 }); P.fill(b1, '--white', { gap: 9 });
  P.ink(b2, { w: 0.8 }); P.fill(b2, '--yellow', { gap: 1.8 });
  P.ink('M20 186l46-16 4 7-46 16z', { w: 1 }); P.fill('M20 186l46-16 4 7-46 16z', '--yellow', { gap: 1.8, angle: 20 }); P.ink('M20 186l-8 5 10-1', { w: 1 });
  P.line(196, 96, 196, 40, { w: 2.2, c: CV('--green') }); P.ink('M186 52l10-14 10 14', { w: 2.2, c: CV('--green') });
  P.line(220, 40, 220, 96, { w: 2.2, c: CV('--red') }); P.ink('M210 84l10 14 10-14', { w: 2.2, c: CV('--red') });
  P.text(184, 120, 'up / down', { s: 13, rot: -6 });
}

/** A hand-drawn megaphone and a speech bubble with scribbled lines. */
function paintOpinion(svg: SVGSVGElement) {
  const seed = 4242;
  paper(svg, seed);
  const P = painter(svg, seed);
  const bub = 'M30 44q0-12 12-12h104q12 0 12 12v52q0 12-12 12H86l-24 22 4-22H42q-12 0-12-12z';
  P.ink(bub, { w: 1.4 }); P.fill(bub, '--sky', { gap: 4, angle: -55 });
  ([[46, 58, 120], [46, 72, 104], [46, 86, 86]] as const).forEach(([x, y, w]) => P.ink(`M${x} ${y}q${w / 3} -3 ${w / 2} 0t${w / 2} 0`, { w: 1.3 }));
  const meg = 'M166 92l36-22v68l-36-22z';
  P.fill(meg, '--red', { gap: 1.9, angle: -20 }); P.ink(meg, { w: 1.3 });
  P.ink(R(152, 94, 16, 20), { w: 1.2 }); P.ink('M158 114l-4 22h8l2-22', { w: 1.1 });
  P.arc(204, 104, 22, 38, -1.2, 1.2, { w: 1.4, c: CV('--blue') }); P.arc(204, 104, 40, 64, -1.1, 1.1, { w: 1.4, c: CV('--yellow') });
  P.text(60, 160, "we'd like to read it", { s: 17, rot: -3 }); P.line(58, 166, 188, 160, { w: 0.9, c: CV('--red') });
}

/** A scrawled note with a pencil underline, used by the scenes below. */
function scrawl(P: ReturnType<typeof painter>, note: string, x: number, y: number, rot = -4) {
  if (!note) return;
  P.text(x, y, note, { s: 14, rot });
  P.line(x - 2, y + 5, x + Math.min(150, note.length * 6.6), y + 1, { w: 0.8, c: CV('--red') });
}

/** What a scene can draw from: the article's slug, its handwritten note, the states it cites and dated actions. */
interface Ctx { slug: string; note: string; fips: Set<string>; states: MapState[]; days: string[] }

/** One state's outline from the map, scaled into a box and hatched. */
function stateShape(svg: SVGSVGElement, P: ReturnType<typeof painter>, d: string, box: [number, number, number, number], colour: string) {
  const probe = document.createElementNS(NS, 'path');
  probe.setAttribute('d', d); svg.appendChild(probe);
  const b = probe.getBBox(); probe.remove();
  const [x, y, w, h] = box, k = Math.min(w / b.width, h / b.height);
  const g = document.createElementNS(NS, 'g');
  g.setAttribute('transform', `translate(${x + (w - b.width * k) / 2 - b.x * k} ${y + (h - b.height * k) / 2 - b.y * k}) scale(${k})`);
  svg.appendChild(g);
  const sub = painter(g as unknown as SVGSVGElement, hseed(d.slice(0, 40)));
  sub.fill(d, colour, { gap: 2.4 / k, w: 1.1 / k, angle: -40, dx: 1 / k, dy: 1 / k });
  sub.ink(d, { w: 1.2 / k, r: 0.7, b: 0.6 });
  void P;
}

/** The Capitol, with the two ways federal policy has moved: an executive order and grant money. */
function paintCapitol(svg: SVGSVGElement, c: Ctx) {
  const seed = hseed(c.slug);
  paper(svg, seed);
  const P = painter(svg, seed);
  const dome = 'M58 82q28-44 56 0z';
  P.fill(dome, '--sky', { gap: 2.2, angle: -48 }); P.ink(dome, { w: 1.3 });
  P.ink(R(62, 82, 48, 12), { w: 1.1 }); P.fill(R(62, 82, 48, 12), '--butter', { gap: 3 });
  P.ink(R(82, 40, 8, 10), { w: 1 }); P.line(86, 40, 86, 24, { w: 1 });
  P.fill('M86 25h13l-3 4 3 4H86z', '--red', { gap: 1.6, angle: 10 }); P.ink('M86 25h13l-3 4 3 4H86z', { w: 0.9 });
  const ped = 'M30 112l56-18 56 18z';
  P.fill(ped, '--butter', { gap: 2.4, angle: -30 }); P.ink(ped, { w: 1.2 });
  P.ink(R(34, 112, 104, 34), { w: 1.2 });
  for (let x = 42; x <= 132; x += 11) P.line(x, 115, x, 144, { w: 1 });
  P.ink(R(24, 146, 124, 7), { w: 1.1 }); P.ink(R(18, 153, 136, 7), { w: 1.1 }); P.fill(R(18, 153, 136, 7), '--metal', { gap: 2.4, angle: 0 });
  // the executive order: a page with a seal
  const eo = 'M160 22l58 4-4 70-58-4z';
  P.fill(eo, '--white', { gap: 7 }); P.ink(eo, { w: 1.2 });
  P.text(166, 40, 'executive', { s: 12, rot: 4 }); P.text(170, 52, 'order', { s: 12, rot: 4 });
  for (let i = 0; i < 3; i++) P.line(166, 62 + i * 8, 208, 65 + i * 8, { w: 0.6, c: '#8c877d' });
  P.circ(200, 84, 14, { w: 1.2, c: CV('--blue') });
  // grant money: an envelope with a dollar sign
  const env = 'M160 110h60v40h-60z';
  P.fill(env, '--mint', { gap: 2.4, angle: -30 }); P.ink(env, { w: 1.2 }); P.ink('M160 110l30 22 30-22', { w: 1.1 });
  P.text(184, 166, 'grants', { s: 14, rot: -3 }); P.text(184, 146, '$', { s: 16, c: CV('--green') });
  scrawl(P, c.note, 30, 188, -2);
}

/** A signed bill and a gavel, with the state's own outline when the article is about one state. */
function paintGavel(svg: SVGSVGElement, c: Ctx) {
  const seed = hseed(c.slug);
  paper(svg, seed);
  const P = painter(svg, seed);
  const page = 'M24 28l92 6-6 136-92-6z';
  P.fill(page, '--white', { gap: 7 }); P.ink(page, { w: 1.3 });
  P.text(34, 50, c.fips.size === 1 ? 'signed into law' : 'an act relating to', { s: 12, rot: 3 });
  for (let i = 0; i < 6; i++) P.line(34 - i * 0.3, 62 + i * 12, 104 - i * 0.3, 66 + i * 12, { w: 0.7, c: '#8c877d' });
  P.ink('M32 146q8-12 14-2t12-4 10 2 12-6', { w: 1.4, c: CV('--blue') });
  P.circ(88, 146, 36, { w: 1.6, c: CV('--red') }); P.text(76, 151, 'LAW', { s: 13, c: CV('--red'), rot: -12 });
  const one = c.fips.size === 1 ? c.states.find((s) => c.fips.has(s.fips)) : undefined;
  if (one) stateShape(svg, P, one.d, [130, 14, 96, 92], '--yellow');
  const head = 'M138 120l36-20 10 17-36 20z';
  P.fill(head, '--wood', { gap: 2, angle: -60 }); P.ink(head, { w: 1.3 });
  P.line(162, 128, 196, 168, { w: 4, c: CV('--wood'), r: 0.6 }); P.line(162, 128, 196, 168, { w: 0.8 });
  P.ink(R(172, 168, 52, 10), { w: 1.2 }); P.fill(R(172, 168, 52, 10), '--wood', { gap: 2, angle: 0 });
  scrawl(P, c.note, 120, 194, -3);
}

/** A teacher at the chalkboard, an apple on the desk, and a small robot crossed out in red. */
function paintClassroom(svg: SVGSVGElement, c: Ctx) {
  const seed = hseed(c.slug);
  paper(svg, seed);
  const P = painter(svg, seed);
  P.fill(R(20, 20, 140, 82), '--green', { gap: 2, w: 1.6, angle: -62, dx: 0, dy: 0 }); P.ink(R(20, 20, 140, 82), { w: 1.4 });
  P.text(30, 54, c.note || 'a person must teach', { s: 16, c: '#fff', rot: -2 });
  P.ink('M34 76q20-6 40 0t40 0', { w: 1.2, c: '#fff6d8' });
  P.line(24, 108, 156, 108, { w: 1.4, c: CV('--wood') });
  P.circ(60, 128, 18, { w: 1.3 }); P.line(60, 137, 60, 168, { w: 1.3 });
  P.line(60, 146, 90, 104, { w: 1.3 }); P.line(60, 146, 46, 160, { w: 1.3 });
  P.line(60, 168, 50, 190, { w: 1.3 }); P.line(60, 168, 72, 190, { w: 1.3 });
  P.fill(R(44, 140, 32, 26), '--blue', { gap: 2.2, angle: -30 });
  P.ink('M100 172h56M104 172v18M152 172v18', { w: 1.2 }); P.fill(R(100, 166, 56, 6), '--wood', { gap: 2, angle: 0 });
  const apple = 'M128 150c-8-6-18 2-14 10 3 6 8 7 14 4 6 3 11 2 14-4 4-8-6-16-14-10z';
  P.fill(apple, '--red', { gap: 1.7, angle: -30 }); P.ink(apple, { w: 1.1 }); P.ink('M128 150q1-6 5-8', { w: 1.1 }); P.ink('M131 145q6-3 9 1q-5 3-9-1z', { w: 0.9, c: CV('--green') });
  P.ink(R(178, 122, 36, 30), { w: 1.3 }); P.fill(R(178, 122, 36, 30), '--metal', { gap: 2.4 });
  P.circ(189, 135, 6, { w: 1 }); P.circ(203, 135, 6, { w: 1 }); P.ink('M188 145h16', { w: 1 });
  P.line(196, 122, 196, 112, { w: 1 }); P.circ(196, 110, 5, { w: 1, c: CV('--red') });
  P.ink(R(182, 152, 28, 26), { w: 1.2 }); P.line(186, 178, 186, 190, { w: 1.2 }); P.line(206, 178, 206, 190, { w: 1.2 });
  P.line(168, 110, 226, 192, { w: 2.4, c: CV('--red') }); P.line(226, 110, 168, 192, { w: 2.4, c: CV('--red') });
}

/** A phone with a student's photo and its altered copy, beside a schoolhouse. */
function paintPhone(svg: SVGSVGElement, c: Ctx) {
  const seed = hseed(c.slug);
  paper(svg, seed);
  const P = painter(svg, seed);
  const body = 'M40 14h76a10 10 0 0 1 10 10v140a10 10 0 0 1-10 10H40a10 10 0 0 1-10-10V24a10 10 0 0 1 10-10z';
  P.ink(body, { w: 1.5 }); P.ink(R(38, 30, 80, 124), { w: 0.9 });
  const face = (y: number, colour: string) => {
    P.ink(R(46, y, 64, 52), { w: 1 }); P.fill(R(46, y, 64, 52), '--sky', { gap: 3, angle: -50 });
    P.circ(78, y + 20, 18, { w: 1.2 }); P.fill(`M78 ${y + 11}a9 9 0 1 1 0 18a9 9 0 1 1 0-18z`, colour, { gap: 1.8 });
    P.ink(`M60 ${y + 52}q18-20 36 0`, { w: 1.2 });
  };
  face(36, '--pink');
  face(96, '--pink');
  P.ink('M44 116h18l4-6 6 12 6-8h34', { w: 1.6, c: CV('--red') }); P.ink('M50 132l56-6', { w: 1.2, c: CV('--red') });
  P.text(129, 124, 'fake', { s: 14, c: CV('--red'), rot: -8 });
  P.ink('M118 70q14 4 14 28', { w: 1.1 }); P.ink('M128 92l4 8 5-8', { w: 1.1 });
  // the school it happens at
  const roof = 'M150 92l38-28 38 28z';
  P.fill(roof, '--red', { gap: 1.9, angle: -25 }); P.ink(roof, { w: 1.3 });
  P.ink(R(156, 92, 64, 52), { w: 1.3 }); P.fill(R(156, 92, 64, 52), '--butter', { gap: 2.6, angle: -40 });
  P.ink(R(180, 116, 16, 28), { w: 1.1 }); P.ink(R(162, 100, 12, 10), { w: 0.9 }); P.ink(R(202, 100, 12, 10), { w: 0.9 });
  P.line(188, 64, 188, 46, { w: 1 }); P.fill('M188 47h12l-3 4 3 4h-12z', '--blue', { gap: 1.6, angle: 10 });
  P.line(148, 144, 228, 144, { w: 1.2 });
  scrawl(P, c.note, 44, 192, -2);
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** Calendar pages for the months the article covers, with each dated action ticked. */
function paintCalendar(svg: SVGSVGElement, c: Ctx) {
  const seed = hseed(c.slug);
  paper(svg, seed);
  const P = painter(svg, seed);
  const months = [...new Set(c.days.map((d) => d.slice(0, 7)))].sort().slice(-2);
  if (!months.length) months.push(new Date().toISOString().slice(0, 7));
  const on = new Set(c.days);
  const W = months.length === 2 ? 104 : 172, cell = W / 7;
  months.forEach((ym, mi) => {
    const x0 = months.length === 2 ? 12 + mi * 112 : 34, y0 = 26;
    const [y, m] = ym.split('-').map(Number);
    const first = new Date(Date.UTC(y, m - 1, 1)).getUTCDay(), len = new Date(Date.UTC(y, m, 0)).getUTCDate();
    const rows = Math.ceil((first + len) / 7);
    P.ink(R(x0, y0, W, 22 + rows * cell + 6), { w: 1.3 });
    P.fill(R(x0, y0, W, 20), '--yellow', { gap: 2.2, angle: -30 }); P.ink(R(x0, y0, W, 20), { w: 1.1 });
    P.text(x0 + 8, y0 + 15, `${MONTHS[m - 1]} ${y}`, { s: 16 });
    for (let dnum = 1; dnum <= len; dnum++) {
      const i = first + dnum - 1, cx = x0 + 2 + (i % 7) * cell, cy = y0 + 24 + Math.floor(i / 7) * cell;
      const iso = `${ym}-${String(dnum).padStart(2, '0')}`;
      if (on.has(iso)) P.ink(`M${cx + cell * 0.18} ${cy + cell * 0.5}l${cell * 0.2} ${cell * 0.25} ${cell * 0.42}-${cell * 0.5}`, { w: 1.5, c: CV('--green') });
      else P.circ(cx + cell / 2 - 1, cy + cell / 2, 1.6, { w: 0.6, c: '#8c877d', r: 0.4 });
    }
  });
  scrawl(P, c.note, 120, 192, -3);
}

/** A folder of student records with a padlock on it. */
function paintLock(svg: SVGSVGElement, c: Ctx) {
  const seed = hseed(c.slug);
  paper(svg, seed);
  const P = painter(svg, seed);
  const folder = 'M26 52h56l10 12h104v104H26z';
  P.fill(folder, '--butter', { gap: 2.4, angle: -40 }); P.ink(folder, { w: 1.4 });
  P.ink(R(42, 40, 70, 18), { w: 0.9 }); P.text(46, 54, 'students', { s: 13 });
  P.arc(120, 106, 40, 44, Math.PI, 2 * Math.PI, { w: 3, c: CV('--metal') }); P.arc(120, 106, 40, 44, Math.PI, 2 * Math.PI, { w: 1 });
  P.ink(R(94, 104, 52, 44), { w: 1.4 }); P.fill(R(94, 104, 52, 44), '--red', { gap: 1.9, angle: -25 });
  P.circ(120, 122, 9, { w: 1.4 }); P.line(120, 126, 120, 136, { w: 2 });
  scrawl(P, c.note, 120, 192, -3);
}

const SCENES: Record<string, (svg: SVGSVGElement, c: Ctx) => void> = {
  capitol: paintCapitol, gavel: paintGavel, classroom: paintClassroom, phone: paintPhone, calendar: paintCalendar, lock: paintLock,
};

/** Paint every <svg class="dr" data-dr=…> under root that has not been painted yet. */
export function paintDrawings(states: MapState[], root: ParentNode = document) {
  for (const svg of root.querySelectorAll<SVGSVGElement>('svg.dr[data-dr]:not([data-done])')) {
    svg.setAttribute('data-done', '');
    const kind = svg.dataset.dr;
    if (kind === 'map') paintMap(svg, svg.dataset.slug!, new Set((svg.dataset.fips ?? '').split(' ').filter(Boolean)), states, svg.dataset.note ?? '');
    else if (kind === 'desk') paintDesk(svg, svg.dataset.slug!);
    else if (kind && kind in SCENES) SCENES[kind](svg, {
      slug: svg.dataset.slug!, note: svg.dataset.note ?? '', states,
      fips: new Set((svg.dataset.fips ?? '').split(' ').filter(Boolean)),
      days: (svg.dataset.days ?? '').split(' ').filter(Boolean),
    });
    else paintOpinion(svg);
  }
}
