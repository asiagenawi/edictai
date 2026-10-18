/**
 * The US map the article drawings are sketched on: every state's outline from the shared map
 * (lib/usmap.ts), scaled into the drawings' 240 x 200 box and thinned, since a pencil line
 * needs far fewer points than the States map.
 */
import { usMap } from './usmap';

const BOX: [[number, number], [number, number]] = [[10, 14], [230, 186]];

export function sketchMap(): { fips: string; d: string }[] {
  const map = usMap();
  const [[x0, y0], [x1, y1]] = BOX;
  const k = Math.min((x1 - x0) / map.width, (y1 - y0) / map.height);
  const ox = x0 + ((x1 - x0) - map.width * k) / 2, oy = y0 + ((y1 - y0) - map.height * k) / 2;
  const r = (n: number) => Math.round(n * 10) / 10;
  return map.states.map((s) => ({
    fips: s.fips,
    d: s.d.split('M').filter(Boolean).map((ring) => {
      const pts = ring.replace(/Z$/, '').split('L').map((p) => p.split(',').map(Number)).map(([x, y]) => [ox + x * k, oy + y * k]);
      const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
      if (Math.max(...xs) - Math.min(...xs) < 1.2 && Math.max(...ys) - Math.min(...ys) < 1.2) return '';
      const out = [pts[0]];
      for (const p of pts.slice(1)) { const q = out[out.length - 1]; if (Math.hypot(p[0] - q[0], p[1] - q[1]) >= 0.8) out.push(p); }
      return out.length < 3 ? '' : 'M' + out.map(([x, y]) => `${r(x)} ${r(y)}`).join('L') + 'Z';
    }).join(''),
  })).filter((s) => s.d);
}
