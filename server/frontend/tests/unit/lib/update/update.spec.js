import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { extractModuleScripts, extractVersion, scriptsChanged, useUpdate } from '@/lib/update'

const INDEX = (hash) => `
<!doctype html>
<html>
  <head>
    <script type="module" crossorigin src="/assets/index-${hash}.js"></script>
    <link rel="stylesheet" href="/assets/index-abc.css" />
  </head>
  <body><div id="app"></div></body>
</html>`

describe('update: pure helpers', () => {
  it('extracts sorted module-script pathnames', () => {
    expect(extractModuleScripts(INDEX('DEADBEEF'))).toEqual(['/assets/index-DEADBEEF.js'])
  })

  it('normalizes absolute script urls to pathnames and ignores non-module scripts', () => {
    const html = `
      <script src="/assets/legacy.js"></script>
      <script type="module" src="https://cdn.example/assets/b-2.js"></script>
      <script type="module" src="/assets/a-1.js"></script>`
    expect(extractModuleScripts(html)).toEqual(['/assets/a-1.js', '/assets/b-2.js'])
  })

  it('detects a changed script set', () => {
    expect(scriptsChanged(['/a.js'], ['/a.js'])).toBe(false)
    expect(scriptsChanged(['/a.js'], ['/b.js'])).toBe(true)
    expect(scriptsChanged(['/a.js'], ['/a.js', '/b.js'])).toBe(true)
  })
})

describe('update: naming the new build', () => {
  it('reads the version a build names in its index.html', () => {
    expect(extractVersion('<meta name="mascope-version" content="v1.8.0" />')).toBe('v1.8.0')
    // Attribute order is the serializer's business, not a contract.
    expect(extractVersion("<meta content='v1.8.0' name='mascope-version'>")).toBe('v1.8.0')
  })

  it('returns null for a build that names no version', () => {
    expect(extractVersion(INDEX('DEADBEEF'))).toBeNull()
    expect(extractVersion('<meta name="mascope-version" content="">')).toBeNull()
  })
})

describe('update store: check()', () => {
  let script

  beforeEach(() => {
    setActivePinia(createPinia())
    // Give the tab a booted entry bundle to compare against.
    script = document.createElement('script')
    script.type = 'module'
    script.src = '/assets/index-BOOTED.js'
    document.head.appendChild(script)
  })

  afterEach(() => {
    script.remove()
    vi.unstubAllGlobals()
  })

  it('flags an update when the deployed entry bundle changed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, text: async () => INDEX('NEWHASH') }))
    )
    const update = useUpdate()

    expect(await update.check()).toBe(true)
    expect(update.available).toBe(true)
  })

  it('records the version the new build names', async () => {
    const named = INDEX('NEWHASH').replace(
      '<head>',
      '<head><meta name="mascope-version" content="v1.8.0">'
    )
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, text: async () => named }))
    )
    const update = useUpdate()

    expect(await update.check()).toBe(true)
    expect(update.version).toBe('v1.8.0')
  })

  it('does not flag when the entry bundle is unchanged', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, text: async () => INDEX('BOOTED') }))
    )
    const update = useUpdate()

    expect(await update.check()).toBe(false)
    expect(update.available).toBe(false)
  })

  it('stays quiet when index.html cannot be read', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('network down')
      })
    )
    const update = useUpdate()

    expect(await update.check()).toBe(false)
    expect(update.available).toBe(false)
  })
})

describe('update store: reload()', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('replaces the cached index.html before reloading the page', async () => {
    const order = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (...args) => {
        order.push(['fetch', ...args])
        return { ok: true }
      })
    )
    const reloadSpy = vi.spyOn(window.location, 'reload').mockImplementation(() => {
      order.push(['reload'])
    })

    await useUpdate().reload()

    expect(order).toEqual([['fetch', window.location.pathname, { cache: 'reload' }], ['reload']])
    expect(reloadSpy).toHaveBeenCalledTimes(1)
  })

  it('still reloads when the cache refresh fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('offline')
      })
    )
    const reloadSpy = vi.spyOn(window.location, 'reload').mockImplementation(() => {})

    await useUpdate().reload()

    expect(reloadSpy).toHaveBeenCalledTimes(1)
  })
})
