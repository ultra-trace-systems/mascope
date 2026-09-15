// The serialized runtime the app is running against: `[meta]` config, mode, env
// and version, as the backend's own Runtime resolved them.
//
// Two sources, in priority order:
//
//  1. `window.__MASCOPE_RUNTIME__`, written by the container at start from the
//     MASCOPE_RUNTIME environment variable (server/frontend/docker-entrypoint.sh
//     -> /runtime-config.js, loaded by index.html before this module).
//  2. `import.meta.env.MASCOPE_RUNTIME`, compiled into the bundle at image
//     build time.
//
// (1) exists because (2) alone is read at the wrong moment. A deployment
// provisioned with `mascope init` keeps its own config layers, which no update
// rewrites, while its images come from the registry built against the repo's -
// so the value the bundle was built with and the value the backend is running
// on can be different revisions of the same file. That split showed up as a UI
// offering features the backend answers 403 for. Reading the runtime the
// container was handed keeps the two halves of every flag in agreement, and
// makes flipping one a restart rather than an image rebuild.
//
// (2) remains the fallback so `vite dev`, unit tests, and a container started
// without MASCOPE_RUNTIME all keep working exactly as before.
function initRuntime() {
  const injected = typeof window !== 'undefined' ? window.__MASCOPE_RUNTIME__ : undefined
  const runtime = injected ?? JSON.parse(import.meta.env.MASCOPE_RUNTIME)
  // build the full api base path
  const host = location.hostname
  const mode = import.meta.env.MODE
  // In a production build the app is served behind nginx (which proxies /api/),
  // so use the page's actual origin -- this works whether served over HTTPS
  // (e.g. https://mascope.app) or plain HTTP (e.g. http://localhost:8080),
  // instead of assuming HTTPS.
  const api_path =
    mode === 'production' ? location.origin : `http://${host}:${runtime.meta.api_port}`
  runtime['api_path'] = api_path

  return runtime
}

export const runtime = initRuntime()

console.log('⚛️ [runtime] initialized', runtime ?? import.meta.env.MASCOPE_RUNTIME)
