import { describe, it, expect, vi, afterEach } from 'vitest'

import { copyText } from '@/lib/clipboard'

// navigator.clipboard exists only in a secure context. Plain HTTP on a LAN
// address - a MASCOPE_TLS=off deployment, a dev host reached over the network -
// has none, and calling it unguarded throws; that broke the About tab's copy
// button on exactly such a host. The helper has to fall back, and report a
// failure rather than pretend it copied.

const originalExecCommand = document.execCommand

afterEach(() => {
  vi.unstubAllGlobals()
  document.execCommand = originalExecCommand
})

describe('copyText', () => {
  it('uses the Clipboard API where it exists', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    document.execCommand = vi.fn()

    expect(await copyText('v1.7.3')).toBe(true)
    expect(writeText).toHaveBeenCalledWith('v1.7.3')
    expect(document.execCommand).not.toHaveBeenCalled()
  })

  it('falls back to the copy command where there is no Clipboard API', async () => {
    vi.stubGlobal('navigator', {})
    let copied
    document.execCommand = vi.fn(() => {
      copied = document.querySelector('textarea')?.value
      return true
    })

    expect(await copyText('v1.7.3')).toBe(true)
    expect(document.execCommand).toHaveBeenCalledWith('copy')
    expect(copied).toBe('v1.7.3')
    // The temporary field does not outlive the copy.
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('falls back when the Clipboard API refuses', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    document.execCommand = vi.fn(() => true)

    expect(await copyText('v1.7.3')).toBe(true)
    expect(document.execCommand).toHaveBeenCalledWith('copy')
  })

  it('puts the temporary field inside the given container', async () => {
    vi.stubGlobal('navigator', {})
    const container = document.createElement('div')
    document.body.appendChild(container)
    let parent
    document.execCommand = vi.fn(() => {
      parent = document.querySelector('textarea')?.parentElement
      return true
    })

    await copyText('v1.7.3', container)

    expect(parent).toBe(container)
    container.remove()
  })

  it('reports failure when neither path copies', async () => {
    vi.stubGlobal('navigator', {})

    document.execCommand = vi.fn(() => false)
    expect(await copyText('v1.7.3')).toBe(false)

    document.execCommand = vi.fn(() => {
      throw new Error('unsupported')
    })
    expect(await copyText('v1.7.3')).toBe(false)
  })
})
