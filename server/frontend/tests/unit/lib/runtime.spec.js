import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Which config the app runs on. The container publishes the backend's own
// serialized runtime as window.__MASCOPE_RUNTIME__ (/runtime-config.js, written
// by docker-entrypoint.sh); the bundle also carries a copy baked in at image
// build time. Those two can be different revisions of the same file on a
// deployment whose config layers are its own, which showed up as a UI offering
// features the backend answered 403 for - so the injected value has to win.
const BAKED = { meta: { api_port: 8090, peak_assignment: false }, mode: 'prod', env: 'default' }
const INJECTED = { meta: { api_port: 9999, peak_assignment: true }, mode: 'prod', env: 'default' }

async function loadRuntime(injected) {
  vi.resetModules()
  vi.stubEnv('MASCOPE_RUNTIME', JSON.stringify(BAKED))
  if (injected === undefined) {
    delete window.__MASCOPE_RUNTIME__
  } else {
    window.__MASCOPE_RUNTIME__ = injected
  }
  return (await import('@/lib/runtime')).runtime
}

describe('runtime', () => {
  beforeEach(() => vi.resetModules())
  afterEach(() => {
    vi.unstubAllEnvs()
    delete window.__MASCOPE_RUNTIME__
  })

  it('prefers the config the container published over the baked-in copy', async () => {
    const runtime = await loadRuntime(INJECTED)

    expect(runtime.meta.peak_assignment).toBe(true)
    expect(runtime.meta.api_port).toBe(9999)
  })

  it('falls back to the baked-in copy when nothing was published', async () => {
    // vite dev, a unit test, and a container started without MASCOPE_RUNTIME all
    // land here - the pre-existing behaviour, which must keep working.
    const runtime = await loadRuntime(undefined)

    expect(runtime.meta.peak_assignment).toBe(false)
    expect(runtime.meta.api_port).toBe(8090)
  })

  it('derives the api path from the published config, not the baked one', async () => {
    // Outside a production build the API is addressed by port, so reading the
    // wrong copy would point the app at the wrong backend entirely.
    const runtime = await loadRuntime(INJECTED)

    expect(runtime.api_path).toBe(`http://${location.hostname}:9999`)
  })
})
