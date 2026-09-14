import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useClipboard } from '@/lib/panes/PaneBrowserSample/stores/clipboard'

// Copy, cut and paste of datasets, batches and samples in the sample browser.
// The payload goes onto the system clipboard so another tab can paste it, and
// the context menus read it back before offering a paste. Neither reading nor
// blanking the clipboard is possible without the async Clipboard API, which a
// page served over plain HTTP from a network address does not have: there the
// write falls back to the copy command, the store keeps its own copy for the
// paste, and nothing may throw.

const BATCH = { sample_batch_id: 'b1', sample_batch_name: 'Morning QC' }
const SAMPLES = [
  { sample_item_id: 's1', sample_batch_id: 'b1', sample_item_name: 'Blank' },
  { sample_item_id: 's2', sample_batch_id: 'b1', sample_item_name: 'Standard' }
]

const originalExecCommand = document.execCommand

beforeEach(() => setActivePinia(createPinia()))

afterEach(() => {
  vi.unstubAllGlobals()
  document.execCommand = originalExecCommand
})

describe('sample browser clipboard without the Clipboard API', () => {
  beforeEach(() => vi.stubGlobal('navigator', {}))

  it('puts the payload on the clipboard through the copy command', async () => {
    let copied
    document.execCommand = vi.fn(() => {
      copied = document.querySelector('textarea')?.value
      return true
    })
    const clipboard = useClipboard()

    await clipboard.copy(BATCH)

    expect(document.execCommand).toHaveBeenCalledWith('copy')
    expect(JSON.parse(copied)).toEqual({ op: 'copy', data: BATCH })
  })

  it('offers what this tab copied or cut for pasting, since it cannot read the clipboard', async () => {
    document.execCommand = vi.fn(() => true)
    const clipboard = useClipboard()

    await clipboard.copy(BATCH)
    await clipboard.read()
    expect(clipboard.op).toBe('copy')
    expect(clipboard.batch).toEqual(BATCH)

    await clipboard.cut(SAMPLES)
    await clipboard.read()
    expect(clipboard.op).toBe('cut')
    expect(clipboard.samples).toEqual(SAMPLES)
  })

  it('stops offering a pasted cut', async () => {
    document.execCommand = vi.fn(() => true)
    const clipboard = useClipboard()
    await clipboard.cut(SAMPLES)

    await clipboard.clear()
    await clipboard.read()

    expect(clipboard.op).toBeUndefined()
    expect(clipboard.samples).toBeNull()
    // Only the async API could blank the system clipboard; the copy command is
    // not used for it.
    expect(document.execCommand).toHaveBeenCalledTimes(1)
  })
})

describe('sample browser clipboard with the Clipboard API', () => {
  it('reads the system clipboard back, so a later copy elsewhere replaces the payload', async () => {
    let systemClipboard = ''
    const writeText = vi.fn(async (text) => {
      systemClipboard = text
    })
    const readText = vi.fn(async () => systemClipboard)
    vi.stubGlobal('navigator', { clipboard: { writeText, readText } })
    const clipboard = useClipboard()

    await clipboard.copy(BATCH)
    await clipboard.read()
    expect(clipboard.batch).toEqual(BATCH)

    systemClipboard = 'some text copied from another application'
    await clipboard.read()
    expect(clipboard.batch).toBeNull()
  })

  it('blanks the system clipboard when a cut is pasted', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    const readText = vi.fn().mockResolvedValue('')
    vi.stubGlobal('navigator', { clipboard: { writeText, readText } })
    const clipboard = useClipboard()
    await clipboard.cut(SAMPLES)

    await clipboard.clear()

    expect(writeText).toHaveBeenLastCalledWith('')
    expect(clipboard.samples).toBeNull()
  })

  it('keeps its own copy when the browser refuses to read the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    const readText = vi.fn().mockRejectedValue(new Error('denied'))
    vi.stubGlobal('navigator', { clipboard: { writeText, readText } })
    const clipboard = useClipboard()

    await clipboard.copy(BATCH)
    await clipboard.read()

    expect(clipboard.batch).toEqual(BATCH)
  })
})
