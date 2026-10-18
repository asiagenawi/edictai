// @ts-check
import { defineConfig } from 'astro/config';

// Static build for GitHub Pages. The repository keeps its name, so the site
// is served from /statescope/.
export default defineConfig({
  site: 'https://asiagenawi.github.io',
  base: '/statescope/',
  trailingSlash: 'always',
  output: 'static',
  build: { format: 'directory' },
  // Scoped styles use :where() so selectors keep the specificity they are written with.
  scopedStyleStrategy: 'where',
});
