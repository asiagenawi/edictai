/**
 * Hand-drawn illustrations for the Articles index. rough.js gives every line a slight wobble and
 * fills colour as loose marker hatching, laid a hair off the ink outline the way a quick sketch
 * is. Each drawing is seeded by its article's slug, so it is the same on every visit.
 *
 * Articles citing state records: a pencil map with those states hatched in brand colours.
 * Research digests: a desk with a chalkboard, a laptop and two arrows. The Opinion invitation:
 * a speech bubble and a megaphone.
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

/** Paint every <svg class="dr" data-dr=…> under root that has not been painted yet. */
export function paintDrawings(states: MapState[], root: ParentNode = document) {
  for (const svg of root.querySelectorAll<SVGSVGElement>('svg.dr[data-dr]:not([data-done])')) {
    svg.setAttribute('data-done', '');
    const kind = svg.dataset.dr;
    if (kind === 'map') paintMap(svg, svg.dataset.slug!, new Set((svg.dataset.fips ?? '').split(' ').filter(Boolean)), states, svg.dataset.note ?? '');
    else if (kind === 'desk') paintDesk(svg, svg.dataset.slug!);
    else paintOpinion(svg);
  }
}
