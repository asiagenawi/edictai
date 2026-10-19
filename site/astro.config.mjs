// @ts-check
import { defineConfig } from 'astro/config';

// Static build for GitHub Pages, served at the custom domain edictai.org.
export default defineConfig({
  site: 'https://edictai.org',
  base: '/',
  trailingSlash: 'always',
  output: 'static',
  build: { format: 'directory' },
  // Scoped styles use :where() so selectors keep the specificity they are written with.
  scopedStyleStrategy: 'where',
});
