/**
 * The United States map for the States index, computed at build time.
 *
 * Projects the us-atlas TopoJSON (states at 1:10m) with the Albers USA
 * composite (the conterminous states on a conic equal-area projection, with
 * Alaska and Hawaii as insets), the same arrangement d3.geoAlbersUsa draws.
 * Returns each state's SVG path, its bounds, a base colour index chosen so
 * neighbouring states never share one, and its mosaic tiles. The same tiles
 * draw the States map and every state page's header outline, so the zoom from
 * one to the other lands exactly.
 */
import atlas from '../data/us-states-10m.json';

type Pt = [number, number];
interface Geometry { id: string; type: string; arcs: number[][] | number[][][] }
interface Topo {
  transform: { scale: Pt; translate: Pt };
  arcs: Pt[][];
  objects: { states: { geometries: Geometry[] } };
}
const topo = atlas as unknown as Topo;
const RAD = Math.PI / 180;

function decodeArcs(): Pt[][] {
  const { scale, translate } = topo.transform;
  return topo.arcs.map((arc) => {
    let x = 0, y = 0;
    return arc.map(([dx, dy]) => {
      x += dx; y += dy;
      return [x * scale[0] + translate[0], y * scale[1] + translate[1]] as Pt;
    });
  });
}

/** d3's conic equal-area raw projection for the given standard parallels (degrees). */
function conicEqualArea(p0: number, p1: number) {
  const sy0 = Math.sin(p0 * RAD), n = (sy0 + Math.sin(p1 * RAD)) / 2;
  const c = 1 + sy0 * (2 * n - sy0), r0 = Math.sqrt(c) / n;
  return (lambda: number, phi: number): Pt => {
    const r = Math.sqrt(c - 2 * n * Math.sin(phi)) / n;
    return [r * Math.sin(lambda * n), r0 - r * Math.cos(lambda * n)];
  };
}

/** One part of the composite: rotate, project, centre, then scale and offset (in units of the scale). */
function part(rotate: number, center: Pt, parallels: Pt, scale: number, offset: Pt) {
  const raw = conicEqualArea(parallels[0], parallels[1]);
  const wrap = (deg: number) => ((((deg + 180) % 360) + 360) % 360) - 180;
  const proj = ([lon, lat]: Pt) => raw(wrap(lon + rotate) * RAD, lat * RAD);
  const [cx, cy] = raw(center[0] * RAD, center[1] * RAD);
  return ([lon, lat]: Pt): Pt => {
    const [x, y] = proj([lon, lat]);
    return [scale * (x - cx) + offset[0], -scale * (y - cy) + offset[1]];
  };
}

// The Albers USA arrangement at unit scale (d3.geoAlbersUsa, scale 1, translate [0, 0]).
const lower48 = part(96, [-0.6, 38.7], [29.5, 45.5], 1, [0, 0]);
const alaska = part(154, [-2, 58.5], [55, 65], 0.35, [-0.307, 0.201]);
const hawaii = part(157, [-3, 19.9], [8, 18], 1, [-0.205, 0.212]);
const FIPS_ALASKA = '02', FIPS_HAWAII = '15';
const projectFor = (fips: string) => (fips === FIPS_ALASKA ? alaska : fips === FIPS_HAWAII ? hawaii : lower48);
// Each part's clip box at unit scale, as d3.geoAlbersUsa sets them; rings that fall wholly outside
// (the far Aleutians, the Northwestern Hawaiian Islands) are left out.
type Box = [Pt, Pt];
const CLIP: Record<string, Box> = {
  [FIPS_ALASKA]: [[-0.425, 0.120], [-0.214, 0.234]],
  [FIPS_HAWAII]: [[-0.214, 0.166], [-0.115, 0.234]],
};
const LOWER48_CLIP: Box = [[-0.455, -0.238], [0.455, 0.238]];
const clipFor = (fips: string) => CLIP[fips] ?? LOWER48_CLIP;
const within = ([x, y]: Pt, [[x0, y0], [x1, y1]]: Box) => x >= x0 && x <= x1 && y >= y0 && y <= y1;

export interface MapState {
  fips: string;
  /** SVG path in the map's own coordinates. */
  d: string;
  bounds: [Pt, Pt];
  /** Bounds of the state's main landmass (rings of at least 1% of its largest ring's area): what its header outline frames. */
  outline: [Pt, Pt];
  /** Index into the seven base colours; neighbours always differ. */
  colour: number;
  /** Mosaic tiles in map units: [x, y, shape 0-4, palette index]. */
  tiles: Tile[];
}

/** Palette order shared with the pages (CSS custom properties of the same names). */
export const PALETTE = ['red', 'yellow', 'blue', 'sky', 'green', 'mint', 'pink', 'graphite', 'butter'];
/** Tile size in map units. */
export const TILE = 7;
export type Tile = [number, number, number, number];

export interface UsMap {
  width: number;
  height: number;
  states: MapState[];
  /** Open space above the Great Lakes, as a fraction of the map's width and height, for the extra buttons. */
  extra: Pt;
}

const COLOURS = 7;
/** Width the map is computed at; the page scales it to fit. */
const WIDTH = 960;

let cached: UsMap | undefined;
export function usMap(): UsMap {
  if (cached) return cached;
  const arcs = decodeArcs();
  const geoms = topo.objects.states.geometries.filter((g) => Number(g.id) <= 56);

  const polygonsOf = (g: Geometry) => (g.type === 'Polygon' ? [g.arcs] : g.arcs) as number[][][];
  const ringOf = (indices: number[], project: (p: Pt) => Pt): Pt[] => {
    const out: Pt[] = [];
    for (const i of indices) {
      const a = i < 0 ? [...arcs[~i]].reverse() : arcs[i];
      out.push(...(out.length ? a.slice(1) : a).map(project));
    }
    return out;
  };
  const projected = geoms.map((g) => ({
    g,
    rings: polygonsOf(g).flat().map((r) => ringOf(r, projectFor(g.id))).filter((r) => r.some((p) => within(p, clipFor(g.id)))),
  }));

  // Fit to WIDTH, then crop to the drawing's own bounds (plus room for the outline stroke).
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  // Only points inside each part's clip box count, so a stray island can't widen the frame.
  for (const { g, rings } of projected) for (const r of rings) for (const p of r) {
    if (!within(p, clipFor(g.id))) continue;
    const [x, y] = p;
    x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
  }
  const PAD = 2;
  const k = (WIDTH - 2 * PAD) / (x1 - x0);
  const fit = ([x, y]: Pt): Pt => [PAD + (x - x0) * k, PAD + (y - y0) * k];
  const height = (y1 - y0) * k + 2 * PAD;
  const f = (n: number) => Math.round(n * 10) / 10;

  // Neighbours share an arc in the topology.
  const owners = new Map<number, number[]>();
  geoms.forEach((g, gi) => {
    for (const poly of polygonsOf(g)) for (const r of poly) for (const i of r) {
      const a = i < 0 ? ~i : i;
      owners.set(a, [...(owners.get(a) ?? []), gi]);
    }
  });
  const neighbours = geoms.map(() => new Set<number>());
  for (const list of owners.values()) for (const a of list) for (const b of list) if (a !== b) neighbours[a].add(b);
  // Seeded so the colouring is the same on every build.
  let seed = 7;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const colour: number[] = [];
  geoms.forEach((_, gi) => {
    const used = new Set([...neighbours[gi]].map((n) => colour[n]));
    let c = Math.floor(rand() * COLOURS);
    for (let t = 0; t < COLOURS && used.has(c); t++) c = (c + 1) % COLOURS;
    colour[gi] = c;
  });

  // Each state's tiles come from its own seeded generator, so they don't depend on any other state.
  const tilesFor = (fips: string, [[x0, y0], [x1, y1]]: [Pt, Pt], base: number): Tile[] => {
    let t = (Number(fips) * 7919 + 13) % 2147483647 || 1;
    const rnd = () => (t = (t * 16807) % 2147483647) / 2147483647;
    const out: Tile[] = [];
    for (let y = Math.floor(y0 / TILE) * TILE; y < y1; y += TILE) for (let x = Math.floor(x0 / TILE) * TILE; x < x1; x += TILE) {
      const k = Math.floor(rnd() * 5);
      out.push([x, y, k, rnd() < 0.82 ? base : Math.floor(rnd() * PALETTE.length)]);
    }
    return out;
  };

  const states = projected.map(({ g, rings }, gi) => {
    const fitted = rings.map((r) => r.map(fit));
    let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
    for (const r of fitted) for (const [x, y] of r) {
      bx0 = Math.min(bx0, x); by0 = Math.min(by0, y); bx1 = Math.max(bx1, x); by1 = Math.max(by1, y);
    }
    const d = fitted.map((r) => 'M' + r.map(([x, y]) => `${f(x)},${f(y)}`).join('L') + 'Z').join('');
    const bounds = [[f(bx0), f(by0)], [f(bx1), f(by1)]] as [Pt, Pt];
    // Far islands (the Aleutians, the smaller Hawaiian islands) would shrink the header outline to a speck,
    // so it frames only rings of at least 1% of the largest ring's area.
    const area = (r: Pt[]) => Math.abs(r.reduce((a, [x, y], i) => { const [u, v] = r[(i + 1) % r.length]; return a + x * v - u * y; }, 0) / 2);
    const areas = fitted.map(area), big = Math.max(...areas);
    let ox0 = Infinity, oy0 = Infinity, ox1 = -Infinity, oy1 = -Infinity;
    fitted.forEach((r, i) => {
      if (areas[i] < big * 0.01) return;
      for (const [x, y] of r) { ox0 = Math.min(ox0, x); oy0 = Math.min(oy0, y); ox1 = Math.max(ox1, x); oy1 = Math.max(oy1, y); }
    });
    const outline = [[f(ox0), f(oy0)], [f(ox1), f(oy1)]] as [Pt, Pt];
    return { fips: g.id, d, bounds, outline, colour: colour[gi], tiles: tilesFor(g.id, bounds, colour[gi]) };
  });

  const [ex, ey] = fit(lower48([-79.8, 47.4]));
  return (cached = { width: WIDTH, height: f(height), states, extra: [ex / WIDTH, ey / height] });
}

/** SVG path data for one tile of size g (insets scale with it: 0.6 at the map's 7 units). */
function tilePath([x, y, k]: Tile, g: number): string {
  const i = g * 0.086, h = g / 2 - i, cx = x + g / 2, cy = y + g / 2;
  const f = (n: number) => Math.round(n * 100) / 100;
  switch (k) {
    case 0: return `M${f(cx - h)} ${f(cy)}a${f(h)} ${f(h)} 0 1 0 ${f(2 * h)} 0a${f(h)} ${f(h)} 0 1 0 ${f(-2 * h)} 0`;
    case 1: return `M${f(x + i)} ${f(y + i)}h${f(g - 2 * i)}v${f(g - 2 * i)}h${f(2 * i - g)}z`;
    case 2: return `M${f(x + i)} ${f(y + g - i)}V${f(y + i)}A${f(g - 2 * i)} ${f(g - 2 * i)} 0 0 1 ${f(x + g - i)} ${f(y + g - i)}z`;
    case 3: return `M${f(cx)} ${f(y + i)}L${f(x + g - i)} ${f(cy)}L${f(cx)} ${f(y + g - i)}L${f(x + i)} ${f(cy)}z`;
    default: return `M${f(cx)} ${f(cy - h)}A${f(h)} ${f(h)} 0 0 1 ${f(cx)} ${f(cy + h)}z`;
  }
}

/**
 * The header outline's bolder tiles: about 17 across the larger side of the state's main landmass, in the
 * same map coordinates as its map tiles. 82% are the state's map base colour; the rest come from the other
 * colours its map tiles use. The States map dissolves into these mid-zoom.
 */
export function boldTiles(fips: string): { size: number; tiles: Tile[] } | null {
  const s = usMap().states.find((x) => x.fips === fips);
  if (!s) return null;
  const [[x0, y0], [x1, y1]] = s.outline, GB = Math.max(x1 - x0, y1 - y0) / 17;
  const cols = [...new Set(s.tiles.map((t) => t[3]))];
  let r = (Number(fips) * 104729 + 7) % 2147483647 || 1;
  const rnd = () => (r = (r * 16807) % 2147483647) / 2147483647;
  const tiles: Tile[] = [];
  for (let y = Math.floor(y0 / GB) * GB; y < y1; y += GB) for (let x = Math.floor(x0 / GB) * GB; x < x1; x += GB) {
    const k = Math.floor(rnd() * 5);
    // Rounded to keep the pages small; the outline SVG and the zoom both draw these same values.
    tiles.push([Math.round(x * 100) / 100, Math.round(y * 100) / 100, k, rnd() < 0.82 ? s.colour : cols[Math.floor(rnd() * cols.length)]]);
  }
  return { size: Math.round(GB * 1000) / 1000, tiles };
}

/** Header outline box: 120px tall, as wide as the state's shape (at least 72px, at most 230px, then shorter). */
export function outlineBox([[x0, y0], [x1, y1]]: [Pt, Pt]): { w: number; h: number } {
  const aspect = (x1 - x0) / (y1 - y0);
  let h = 120, w = Math.max(72, 120 * aspect);
  if (w > 230) { w = 230; h = 230 / aspect; }
  return { w: Math.round(w), h: Math.round(h) };
}

/**
 * A state's header outline as SVG: its bold tiles clipped to its shape, the viewBox framing its main landmass.
 * The page sizes the box with outlineBox and insets the SVG 2px; the States map's zoom lands on that same frame.
 */
export function stateOutline(fips: string, label: string): { svg: string; w: number; h: number } | null {
  const s = usMap().states.find((x) => x.fips === fips), bold = boldTiles(fips);
  if (!s || !bold) return null;
  const [[x0, y0], [x1, y1]] = s.outline;
  const byColour = new Map<number, string[]>();
  for (const t of bold.tiles) byColour.set(t[3], [...(byColour.get(t[3]) ?? []), tilePath(t, bold.size)]);
  const id = `so-${fips}`;
  const paths = [...byColour].map(([c, ds]) => `<path fill="var(--${PALETTE[c]})" d="${ds.join('')}"/>`).join('');
  const r = (n: number) => Math.round(n * 100) / 100;
  const svg = `<svg viewBox="${r(x0)} ${r(y0)} ${r(x1 - x0)} ${r(y1 - y0)}" role="img" aria-label="${label}"><defs><clipPath id="${id}"><path d="${s.d}"/></clipPath></defs><g clip-path="url(#${id})">${paths}</g></svg>`;
  return { svg, ...outlineBox(s.outline) };
}
