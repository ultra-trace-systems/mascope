import { describe, it, expect, vi } from 'vitest'

import {
  counterpartSamplePeakId,
  createPeakFocusFollower
} from '@/stores/data/modules/peakFocusFollow'

// The follower is a factory over injected dependencies, so every ordering rule
// below is exercised with hand-resolved promises -- no component, no pinia and
// no clock, which is what makes the race cases deterministic rather than timed.

/** A peak store stand-in whose focus writes are observable. */
const makePeak = ({ pending = false, error = null, focusedId = null, list = [] } = {}) => {
  const peak = {
    pending,
    error,
    focusedId,
    list,
    focus: vi.fn((record) => {
      peak.focusedId = record.peak_id
    })
  }
  return peak
}

/** A fetch whose every call is resolved by hand, in whatever order a test wants. */
const gatedFetch = () => {
  const calls = []
  const fetch = vi.fn(
    (args) => new Promise((resolve, reject) => calls.push({ args, resolve, reject }))
  )
  return { fetch, calls }
}

/**
 * A settle barrier the test releases explicitly. Releasing before anything is
 * waiting is allowed, so a test can set up the world it wants the follow to
 * wake into and then let it through in one step.
 */
const gatedSettle = () => {
  let released = false
  const waiting = []
  const settled = () =>
    new Promise((resolve) => {
      if (released) resolve()
      else waiting.push(resolve)
    })
  return {
    settled,
    release: () => {
      released = true
      waiting.splice(0).forEach((resolve) => resolve())
    }
  }
}

/** Let every already-resolved promise continuation run. */
const drain = () => new Promise((resolve) => setTimeout(resolve, 0))

const occurrence = (sampleItemId, samplePeakId) => ({
  batch_peak_id: 'bp-1',
  sample_item_id: sampleItemId,
  sample_peak_id: samplePeakId
})

describe('counterpartSamplePeakId', () => {
  it('returns the counterpart peak id for the target sample', () => {
    expect(counterpartSamplePeakId([occurrence('s-2', 'p-2')], 's-2')).toBe('p-2')
  })

  it('compares ids as strings, because the two feeds disagree on the type', () => {
    expect(counterpartSamplePeakId([{ sample_item_id: 2, sample_peak_id: 77 }], '2')).toBe('77')
  })

  it('ignores an occurrence that belongs to another sample', () => {
    expect(counterpartSamplePeakId([occurrence('s-3', 'p-3')], 's-2')).toBeNull()
  })

  it('is null for an empty answer or a non-array', () => {
    expect(counterpartSamplePeakId([], 's-2')).toBeNull()
    expect(counterpartSamplePeakId(null, 's-2')).toBeNull()
    expect(counterpartSamplePeakId(undefined, 's-2')).toBeNull()
  })

  it('skips a row with no peak id rather than focusing nothing', () => {
    const rows = [{ sample_item_id: 's-2', sample_peak_id: null }, occurrence('s-2', 'p-2')]
    expect(counterpartSamplePeakId(rows, 's-2')).toBe('p-2')
  })
})

describe('createPeakFocusFollower', () => {
  /** The common case: a peak focused in s-1, the user opens s-2. */
  const setup = ({ peakOptions, focusEpoch = () => 0 } = {}) => {
    const peak = makePeak({ list: [{ peak_id: 'p-2', mz: 200 }], ...peakOptions })
    const sample = { focusedId: 's-2' }
    const { fetch, calls } = gatedFetch()
    const { settled, release } = gatedSettle()
    const follower = createPeakFocusFollower({
      fetchCounterpart: fetch,
      settled,
      peak,
      sample,
      focusEpoch
    })
    const run = () =>
      follower.follow({ fromSampleItemId: 's-1', fromPeakId: 'p-1', toSampleItemId: 's-2' })
    return { follower, peak, sample, fetch, calls, release, run }
  }

  it('focuses the counterpart peak once the peak store has settled', async () => {
    const { peak, calls, release, run, fetch } = setup()

    const done = run()
    expect(fetch).toHaveBeenCalledWith({
      sampleItemId: 's-1',
      samplePeakId: 'p-1',
      targetSampleItemId: 's-2'
    })

    calls[0].resolve([occurrence('s-2', 'p-2')])
    await drain()
    // Still waiting on the store: nothing may be written before the list is
    // the target sample's.
    expect(peak.focus).not.toHaveBeenCalled()

    release()
    expect(await done).toBe(true)
    expect(peak.focus).toHaveBeenCalledWith({ peak_id: 'p-2', mz: 200 })
  })

  it('joins the counterpart id to the peak list as strings', async () => {
    const { peak, calls, release, run } = setup({
      peakOptions: { list: [{ peak_id: 77, mz: 200 }] }
    })

    const done = run()
    calls[0].resolve([{ sample_item_id: 's-2', sample_peak_id: '77' }])
    release()

    expect(await done).toBe(true)
    expect(peak.focus).toHaveBeenCalledWith({ peak_id: 77, mz: 200 })
  })

  it('does nothing when the peak has no counterpart in the new sample', async () => {
    const { peak, calls, release, run } = setup()

    const done = run()
    calls[0].resolve([])
    release()

    expect(await done).toBe(false)
    expect(peak.focus).not.toHaveBeenCalled()
  })

  it('stays silent when the lookup fails', async () => {
    const { peak, calls, release, run } = setup()

    const done = run()
    calls[0].reject(new Error('boom'))
    release()

    await expect(done).resolves.toBe(false)
    expect(peak.focus).not.toHaveBeenCalled()
  })

  it('does nothing when the counterpart is absent from the settled list', async () => {
    const { peak, calls, release, run } = setup()

    const done = run()
    calls[0].resolve([occurrence('s-2', 'p-not-loaded')])
    release()

    expect(await done).toBe(false)
    // Never focus by a bare id: focusing one the list does not hold CLEARS.
    expect(peak.focus).not.toHaveBeenCalled()
  })

  it('lets the newest follow win when samples are switched through rapidly', async () => {
    const peak = makePeak({
      list: [
        { peak_id: 'p-2', mz: 200 },
        { peak_id: 'p-3', mz: 300 }
      ]
    })
    const sample = { focusedId: 's-3' }
    const { fetch, calls } = gatedFetch()
    const { settled, release } = gatedSettle()
    const follower = createPeakFocusFollower({
      fetchCounterpart: fetch,
      settled,
      peak,
      sample,
      focusEpoch: () => 0
    })

    // s-1 -> s-2 -> s-3, both follows anchored on the peak's own sample.
    const first = follower.follow({
      fromSampleItemId: 's-1',
      fromPeakId: 'p-1',
      toSampleItemId: 's-2'
    })
    const second = follower.follow({
      fromSampleItemId: 's-1',
      fromPeakId: 'p-1',
      toSampleItemId: 's-3'
    })

    // Both lookups come back before either store settle, so the superseded
    // one is still alive at the write guard -- the ordering that would
    // otherwise focus a peak from a sample the user has already left.
    calls[1].resolve([occurrence('s-3', 'p-3')])
    calls[0].resolve([occurrence('s-2', 'p-2')])
    await drain()
    release()

    expect(await first).toBe(false)
    expect(await second).toBe(true)
    expect(peak.focus).toHaveBeenCalledTimes(1)
    expect(peak.focus).toHaveBeenCalledWith({ peak_id: 'p-3', mz: 300 })
  })

  it('does not focus after the sample was switched away while the lookup was in flight', async () => {
    const { peak, sample, calls, release, run } = setup()

    const done = run()
    calls[0].resolve([occurrence('s-2', 'p-2')])
    // Something we do not drive moved the sample on: its own handler owns the
    // focus now.
    sample.focusedId = 's-9'
    release()

    expect(await done).toBe(false)
    expect(peak.focus).not.toHaveBeenCalled()
  })

  it('does not focus when the store is still pending after the settle backstop fired', async () => {
    const { peak, calls, release, run } = setup({ peakOptions: { pending: true } })

    const done = run()
    calls[0].resolve([occurrence('s-2', 'p-2')])
    release()

    expect(await done).toBe(false)
    expect(peak.focus).not.toHaveBeenCalled()
  })

  it('does not focus when the reload failed and left the previous sample rows', async () => {
    const { peak, calls, release, run } = setup({ peakOptions: { error: new Error('nope') } })

    const done = run()
    calls[0].resolve([occurrence('s-2', 'p-2')])
    release()

    expect(await done).toBe(false)
    expect(peak.focus).not.toHaveBeenCalled()
  })

  it('yields to a peak someone else focused while the lookup was in flight', async () => {
    const { peak, calls, release, run } = setup()

    const done = run()
    calls[0].resolve([occurrence('s-2', 'p-2')])
    // The batch-chart click-through, or the user clicking the ledger.
    peak.focusedId = 'p-clicked'
    release()

    expect(await done).toBe(false)
    expect(peak.focus).not.toHaveBeenCalled()
  })

  it('does not refill a selection the user deliberately cleared', async () => {
    // Focus transitions behind the follow: the reload's own unfocus (1), the
    // user focusing a peak (2), the user clearing it again (3). Only the first
    // is expected, so the vacancy at the end is the user's choice.
    let epoch = 0
    const { peak, calls, release, run } = setup({ focusEpoch: () => epoch })

    const done = run()
    calls[0].resolve([occurrence('s-2', 'p-2')])
    epoch = 3
    release()

    expect(await done).toBe(false)
    expect(peak.focus).not.toHaveBeenCalled()
  })

  it('accepts the one focus transition the reload itself makes', async () => {
    let epoch = 0
    const { peak, calls, release, run } = setup({ focusEpoch: () => epoch })

    const done = run()
    calls[0].resolve([occurrence('s-2', 'p-2')])
    epoch = 1
    release()

    expect(await done).toBe(true)
    expect(peak.focus).toHaveBeenCalledOnce()
  })

  it('cancel stands down a follow that is already in flight', async () => {
    const { follower, peak, calls, release, run } = setup()

    const done = run()
    follower.cancel()
    calls[0].resolve([occurrence('s-2', 'p-2')])
    release()

    expect(await done).toBe(false)
    expect(peak.focus).not.toHaveBeenCalled()
  })
})
