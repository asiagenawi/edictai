import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: '/statescope/',
  build: {
    rollupOptions: {
      output: {
        // Keep the map path lean: charts and markdown load only when a user
        // opens Trends or asks a question, and each vendor chunk caches
        // independently of app code.
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'vendor-react'
          if (/[\\/]node_modules[\\/](react-simple-maps|d3-|topojson)/.test(id)) return 'vendor-map'
          if (/[\\/]node_modules[\\/](recharts|victory-|lodash)/.test(id)) return 'vendor-charts'
          if (/[\\/]node_modules[\\/](react-markdown|remark-|micromark|mdast-|unist-|vfile|hast-|unified|decode-named|character-entities|property-information|space-separated|comma-separated|bail|is-plain-obj|trough|zwitch|html-void|devlop|estree|ccount|markdown-table|longest-streak)/.test(id)) return 'vendor-markdown'
        },
      },
    },
  },
})
