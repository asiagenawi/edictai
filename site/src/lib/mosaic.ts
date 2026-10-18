/**
 * A state's outline drawn as a mosaic of brand shapes, computed at build time.
 *
 * Reads the us-atlas TopoJSON (states at 1:10m), projects one state (or the
 * conterminous United States) with a Mercator projection fitted to a square,
 * and fills each grid cell whose centre falls inside the outline with a
 * circle, square, quarter circle, diamond, or half circle in a palette colour.
 * The result is static SVG markup, so the page needs no map library.
 */
import atlas from '../data/us-states-10m.json';

type Pt = [number, number];
interface Topo {
  transform: { scale: Pt; translate: Pt };
  arcs: Pt[][];
  objects: { states: { geometries: { id: string; type: string; arcs: number[][] | number[][][] }[] } };
}
const topo = atlas as unknown as Topo;

const PALETTE = ['red', 'yellow', 'blue', 'sky', 'green', 'mint', 'pink', 'graphite', 'butter'];
const SIZE = 168;
const PAD = 10;
const CELL = 9;

let decoded: Pt[][] | undefined;
/** Arcs in longitude and latitude (TopoJSON stores them quantized and delta-encoded). */
function arcs(): Pt[][] {
  if (decoded) return decoded;
  const { scale, translate } = topo.transform;
  return (decoded = topo.arcs.map((arc) => {
    let x = 0, y = 0;
    return arc.map(([dx, dy]) => {
      x += dx; y += dy;
      return [x * scale[0] + translate[0], y * scale[1] + translate[1]] as Pt;
    });
  }));
}

function ring(indices: number[]): Pt[] {
  const out: Pt[] = [];
  for (const i of indices) {
    const a = i < 0 ? [...arcs()[~i]].reverse() : arcs()[i];
    out.push(...(out.length ? a.slice(1) : a));
  }
  return out;
}

/** Rings of every polygon of the given FIPS codes; holes are kept as rings and handled by even-odd filling. */
function rings(fips: string[]): Pt[][] {
  const out: Pt[][] = [];
  for (const g of topo.objects.states.geometries) {
    if (!fips.includes(g.id)) continue;
    const polys = (g.type === 'Polygon' ? [g.arcs] : g.arcs) as number[][][];
    for (const poly of polys) for (const r of poly) out.push(ring(r));
  }
  return out;
}

function inside([x, y]: Pt, rs: Pt[][]): boolean {
  let hit = false;
  for (const r of rs) {
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const [xi, yi] = r[i], [xj, yj] = r[j];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
    }
  }
  return hit;
}

/** Small seeded generator so each state's mosaic is the same on every build. */
function rng(seed: number) {
  let x = seed >>> 0 || 1;
  return () => {
    x = Math.imul(x ^ (x >>> 15), 2246822507);
    x = Math.imul(x ^ (x >>> 13), 3266489909);
    return ((x ^= x >>> 16) >>> 0) / 4294967296;
  };
}

function shape(k: number, x: number, y: number): string {
  const g = CELL, h = g / 2 - 1, cx = x + g / 2, cy = y + g / 2;
  const f = (n: number) => +n.toFixed(2);
  switch (k) {
    case 0: return `M${f(cx - h)} ${f(cy)}a${f(h)} ${f(h)} 0 1 0 ${f(2 * h)} 0a${f(h)} ${f(h)} 0 1 0 ${f(-2 * h)} 0`;
    case 1: return `M${f(x + 1)} ${f(y + 1)}h${g - 2}v${g - 2}h${2 - g}z`;
    case 2: return `M${f(x + 1)} ${f(y + g - 1)}V${f(y + 1)}A${g - 2} ${g - 2} 0 0 1 ${f(x + g - 1)} ${f(y + g - 1)}z`;
    case 3: return `M${f(cx)} ${f(y + 1)}L${f(x + g - 1)} ${f(cy)}L${f(cx)} ${f(y + g - 1)}L${f(x + 1)} ${f(cy)}z`;
    default: return `M${f(cx)} ${f(cy - h)}A${f(h)} ${f(h)} 0 0 1 ${f(cx)} ${f(cy + h)}z`;
  }
}

/**
 * SVG markup (a 168-unit square) for a jurisdiction, or null when there is no
 * outline. `fips` is the state's FIPS code; null draws the conterminous US.
 */
export function mosaicSvg(fips: string | null, label: string): string | null {
  const codes = fips
    ? [fips]
    : topo.objects.states.geometries.map((g) => g.id).filter((id) => Number(id) <= 56 && id !== '02' && id !== '15');
  // Alaska's Aleutians cross the antimeridian; keep its longitudes continuous.
  const wrap = (lon: number) => (fips === '02' && lon > 0 ? lon - 360 : lon);
  const merc = ([lon, lat]: Pt): Pt => [
    (wrap(lon) * Math.PI) / 180,
    -Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)),
  ];
  const projected = rings(codes).map((r) => r.map(merc));
  if (!projected.length) return null;

  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const r of projected) for (const [x, y] of r) {
    x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
  }
  const k = (SIZE - 2 * PAD) / Math.max(x1 - x0, y1 - y0);
  const ox = PAD + (SIZE - 2 * PAD - (x1 - x0) * k) / 2, oy = PAD + (SIZE - 2 * PAD - (y1 - y0) * k) / 2;
  const fitted = projected.map((r) => r.map(([x, y]) => [ox + (x - x0) * k, oy + (y - y0) * k] as Pt));

  const rand = rng(Number(fips ?? '99') * 7919 + 6);
  const byColour = new Map<string, string[]>();
  for (let y = 0; y < SIZE; y += CELL) {
    for (let x = 0; x < SIZE; x += CELL) {
      if (!inside([x + CELL / 2, y + CELL / 2], fitted)) continue;
      const colour = PALETTE[Math.floor(rand() * PALETTE.length)];
      const d = shape(Math.floor(rand() * 5), x, y);
      byColour.set(colour, [...(byColour.get(colour) ?? []), d]);
    }
  }
  const paths = [...byColour].map(([c, ds]) => `<path fill="var(--${c})" d="${ds.join('')}"/>`).join('');
  return `<svg viewBox="0 0 ${SIZE} ${SIZE}" role="img" aria-label="${label}">${paths}</svg>`;
}
