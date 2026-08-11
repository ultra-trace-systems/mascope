import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Where a locally running `mkdocs serve` answers (repo root: `npm run docs`,
// or `uv run mkdocs serve`). Because mkdocs.yml sets site_url with a /docs/
// path, mkdocs serve already serves under /docs/ - no path rewrite needed.
const docsDevServer = process.env.MASCOPE_DOCS_URL || 'http://localhost:8000'

// Without mkdocs serve running, answer /docs/ requests with a hint instead of
// a connection-refused error. The in-app help store probes
// /docs/help-content.json on startup, so this also keeps that probe quiet.
const docsProxyFallback = (proxy) =>
  proxy.on('error', (err, req, res) => {
    if (!res || res.headersSent || !res.writeHead) return
    res.writeHead(503, { 'Content-Type': 'text/html' })
    res.end(
      '<h1>Docs dev server is not running</h1>' +
        `<p>The dev server proxies <code>/docs/</code> to <code>${docsDevServer}</code>.</p>` +
        '<p>Start it from the repo root with <code>npm run docs</code> ' +
        '(in server/frontend) or <code>uv run mkdocs serve</code>.</p>'
    )
  })

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      // This alias configuration helps in resolving paths relative to the src directory.
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    // expose dev server to local network if option is set
    host: process.env.MASCOPE_DEVHOST ? '0.0.0.0' : 'localhost',
    // accept tailnet hostnames, so an instance on a remote agent host can be
    // browsed from other tailnet machines via `tailscale serve` (which proxies
    // to localhost, but forwards the *.ts.net Host header)
    allowedHosts: ['.ts.net'],
    // listen port; overridden per-instance so several checkouts can run
    // their own dev server on one machine (see `mascope dev run --instance`)
    port: Number(process.env.MASCOPE_FRONTEND_PORT) || 5173,
    proxy: {
      // The user docs, same path as in production (nginx serves the built
      // site at /docs/). With `mkdocs serve` running, in-app "Learn more"
      // links work in dev and docs edits live-reload.
      '/docs': { target: docsDevServer, configure: docsProxyFallback },
      // mkdocs' live-reload long-poll endpoint sits at the server root.
      '/livereload': { target: docsDevServer, configure: docsProxyFallback }
    }
  },
  build: {
    chunkSizeWarningLimit: 600,
    cssCodeSplit: false,
    target: 'esnext'
  },
  envPrefix: 'MASCOPE_'
})
