import { describe, it, expect, vi } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'

import { usePendingRowFocus } from '@/lib/ledger/usePendingRowFocus.js'

// A ledger row clicked while the sample's peaks load waits as pending and is
// focused once they land for the same sample (#3588).

function setup({ loading = true, peaks = [] } = {}) {
  const state = {
    loading: ref(loading),
    error: ref(null),
    sample: ref('s-1'),
    peaks: ref(peaks)
  }
  const focus = vi.fn()
  const scope = effectScope()
  const pending = scope.run(() =>
    usePendingRowFocus({
      loading: () => state.loading.value,
      failed: () => state.error.value != null,
      sampleId: () => state.sample.value,
      find: (id) => state.peaks.value.find((p) => String(p.peak_id) === String(id)) ?? null,
      focus
    })
  )
  // Lands the list: the rows first, then the flag, as the loader writes them.
  const land = async (list, { error = null } = {}) => {
    state.peaks.value = list
    state.error.value = error
    state.loading.value = false
    await nextTick()
  }
  return { state, focus, pending, land, scope }
}

const P1 = { peak_id: 1, mz: 100 }
const P2 = { peak_id: 2, mz: 200 }

describe('usePendingRowFocus', () => {
  it('focuses at once when the list is in', () => {
    const { pending, focus } = setup({ loading: false, peaks: [P1] })
    const then = vi.fn()

    expect(pending.request('1', then)).toBe(true)
    expect(focus).toHaveBeenCalledWith(P1)
    expect(then).toHaveBeenCalledTimes(1)
    expect(pending.pending.value).toBeNull()
  })

  it('does nothing for a peak the landed list lacks', () => {
    const { pending, focus } = setup({ loading: false, peaks: [P1] })
    const then = vi.fn()

    expect(pending.request(9, then)).toBe(false)
    expect(focus).not.toHaveBeenCalled()
    expect(then).not.toHaveBeenCalled()
    expect(pending.pending.value).toBeNull()
  })

  it('keeps a click made while the list loads, and focuses it when the list lands', async () => {
    const { pending, focus, land } = setup()
    const then = vi.fn()

    expect(pending.request(2, then)).toBe(false)
    expect(focus).not.toHaveBeenCalled()
    expect(pending.pending.value).toMatchObject({ peakId: 2, sampleId: 's-1' })

    await land([P1, P2])
    expect(focus).toHaveBeenCalledTimes(1)
    expect(focus).toHaveBeenCalledWith(P2)
    expect(then).toHaveBeenCalledTimes(1)
    expect(pending.pending.value).toBeNull()
  })

  it('drops the click when the sample changes before the list lands', async () => {
    // The store still holds a list with a peak of that id: the one it had.
    const { state, pending, focus, land } = setup({ peaks: [P1] })
    const then = vi.fn()
    pending.request(1, then)

    state.sample.value = 's-2'
    await nextTick()
    expect(pending.pending.value).toBeNull()

    // The other sample's list holds a peak of that id: still not focused.
    await land([P1])
    expect(focus).not.toHaveBeenCalled()
    expect(then).not.toHaveBeenCalled()
  })

  it('keeps only the second of two clicks made while the list loads', async () => {
    const { pending, focus, land } = setup()
    const first = vi.fn()
    const second = vi.fn()
    pending.request(1, first)
    pending.request(2, second)
    expect(pending.pending.value).toMatchObject({ peakId: 2 })

    await land([P1, P2])
    expect(focus).toHaveBeenCalledTimes(1)
    expect(focus).toHaveBeenCalledWith(P2)
    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledTimes(1)
  })

  it('drops the click when cleared, a deselect', async () => {
    const { pending, focus, land } = setup()
    pending.request(1)
    pending.clear()

    await land([P1])
    expect(focus).not.toHaveBeenCalled()
  })

  it('drops the click when the list fails to load', async () => {
    const { pending, focus, land } = setup({ peaks: [P1] })
    pending.request(1)

    // A failed load keeps the rows it held before; they say nothing here.
    await land([P1], { error: new Error('down') })
    expect(focus).not.toHaveBeenCalled()
    expect(pending.pending.value).toBeNull()
  })

  it('drops the click when the landed list lacks its peak', async () => {
    const { pending, focus, land } = setup()
    pending.request(9)

    await land([P1])
    expect(focus).not.toHaveBeenCalled()
    expect(pending.pending.value).toBeNull()
  })

  it('focuses once only, not again on a later reload', async () => {
    const { state, pending, focus, land } = setup()
    pending.request(1)
    await land([P1])

    state.loading.value = true
    await nextTick()
    await land([P1])
    expect(focus).toHaveBeenCalledTimes(1)
  })
})
