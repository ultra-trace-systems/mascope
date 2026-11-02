import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

import { loadTurnstile, resetTurnstileLoader, TURNSTILE_SCRIPT_URL } from '@/lib/turnstile'

// Cloudflare's script, loaded once and only when asked for. The script
// elements are caught before they reach the document, which would try to fetch
// them, and the tests fire their load and error events by hand.

let added = []
const scripts = () => added

describe('loadTurnstile', () => {
  beforeEach(() => {
    resetTurnstileLoader()
    delete window.turnstile
    added = []
    vi.spyOn(document.head, 'appendChild').mockImplementation((el) => {
      added.push(el)
      return el
    })
  })

  afterEach(() => {
    delete window.turnstile
    vi.restoreAllMocks()
  })

  it('adds the script in explicit mode, once for any number of callers', async () => {
    const first = loadTurnstile()
    const second = loadTurnstile()

    expect(scripts()).toHaveLength(1)
    expect(scripts()[0].src).toBe(TURNSTILE_SCRIPT_URL)

    window.turnstile = { render: () => 'w' }
    scripts()[0].onload()
    expect(await first).toBe(window.turnstile)
    expect(await second).toBe(window.turnstile)
  })

  it('adds no script when the API is already there', async () => {
    window.turnstile = { render: () => 'w' }
    expect(await loadTurnstile()).toBe(window.turnstile)
    expect(scripts()).toHaveLength(0)
  })

  it('tries again after a failed load', async () => {
    const failed = loadTurnstile()
    scripts()[0].onerror()
    await expect(failed).rejects.toThrow('could not be loaded')

    loadTurnstile()
    expect(scripts()).toHaveLength(2)
  })
})
