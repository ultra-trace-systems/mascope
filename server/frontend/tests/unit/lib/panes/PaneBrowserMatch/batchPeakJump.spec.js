import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const post = vi.fn()
vi.mock('@/api', () => ({ api: { http: { post: (...args) => post(...args) } } }))

let app
vi.mock('@/stores', () => ({ useApp: () => app }))

const focusSamplePeak = vi.fn(() => Promise.resolve('peak'))
vi.mock('@/lib/panes/PaneBrowserSample/stores/focusSamplePeak.js', () => ({
  focusSamplePeak: (...args) => focusSamplePeak(...args)
}))

const { brightestMember, useBatchPeakJump } =
  await import('@/lib/panes/PaneBrowserMatch/stores/batchPeakJump.js')

const SAMPLES = [
  { sample_item_id: 's-1', sample_item_name: 'S1' },
  { sample_item_id: 's-2', sample_item_name: 'S2' },
  { sample_item_id: 's-3', sample_item_name: 'S3' }
]

/** A series record as the route serves it: parallel arrays over the members. */
const series = (ids, intensities, peaks) => ({
  batch_peak_id: 'bp-1',
  peak_series: { sample_item_ids: ids, intensities, sample_peak_ids: peaks }
})

function makeApp({ viewingId = null } = {}) {
  return {
    data: {
      batch: { focusedId: 'b-1' },
      batchPeakRun: { viewingId },
      sample: { list: SAMPLES }
    },
    ui: { notification: { push: vi.fn() } }
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  app = makeApp()
})

describe('brightestMember', () => {
  it('picks the member with the highest intensity, with its peak', () => {
    expect(
      brightestMember(series(['s-1', 's-2', 's-3'], [10, 300, 42], ['p1', 'p2', 'p3']))
    ).toEqual({ sample_item_id: 's-2', sample_peak_id: 'p2', intensity: 300 })
  })

  it('skips members without an intensity and keeps the first of equals', () => {
    expect(
      brightestMember(series(['s-1', 's-2', 's-3'], [null, 7, 7], ['p1', 'p2', 'p3']))
    ).toMatchObject({ sample_item_id: 's-2' })
  })

  it('answers nothing for an empty or absent series', () => {
    expect(brightestMember(null)).toBeNull()
    expect(brightestMember(series([], [], []))).toBeNull()
    expect(brightestMember(series(['s-1'], [null], ['p1']))).toBeNull()
  })

  it('tolerates a series without peak ids', () => {
    expect(
      brightestMember({ peak_series: { sample_item_ids: ['s-1'], intensities: [1] } })
    ).toEqual({ sample_item_id: 's-1', sample_peak_id: null, intensity: 1 })
  })
})

describe('useBatchPeakJump', () => {
  it('reads the series of the one anchor and opens its brightest sample on that peak', async () => {
    post.mockResolvedValue([series(['s-1', 's-2'], [10, 300], ['p1', 'p2'])])
    const jump = useBatchPeakJump()

    await jump.jumpToBrightest({ batch_peak_id: 'bp-1', n_present: 2 })

    expect(post).toHaveBeenCalledWith(
      '/batch-peaks/records/series',
      { sample_batch_id: 'b-1', batch_peak_ids: ['bp-1'] },
      expect.objectContaining({ use: 'read', errors: 'inline' })
    )
    expect(focusSamplePeak).toHaveBeenCalledWith(app, SAMPLES[1], 'p2')
    expect(app.ui.notification.push).not.toHaveBeenCalled()
    expect(jump.pendingId).toBeNull()
  })

  it('reads the run on screen, so an earlier run opens where it was brightest then', async () => {
    app = makeApp({ viewingId: 'run-old' })
    post.mockResolvedValue([series(['s-1'], [10], ['p1'])])
    const jump = useBatchPeakJump()

    await jump.jumpToBrightest({ batch_peak_id: 'bp-1' })

    expect(post.mock.calls[0][1]).toEqual({
      sample_batch_id: 'b-1',
      batch_peak_ids: ['bp-1'],
      batch_peak_run_id: 'run-old'
    })
  })

  it('marks the row while the jump is in flight and refuses a second one meanwhile', async () => {
    let release
    post.mockReturnValue(new Promise((resolve) => (release = resolve)))
    const jump = useBatchPeakJump()

    const first = jump.jumpToBrightest({ batch_peak_id: 'bp-1' })
    expect(jump.pendingId).toBe('bp-1')
    await jump.jumpToBrightest({ batch_peak_id: 'bp-2' })
    expect(post).toHaveBeenCalledTimes(1)

    release([series(['s-1'], [10], ['p1'])])
    await first
    expect(jump.pendingId).toBeNull()
  })

  it('says when no loaded sample holds the peak, and opens nothing', async () => {
    post.mockResolvedValue([series(['s-9'], [10], ['p9'])])
    const jump = useBatchPeakJump()

    await jump.jumpToBrightest({ batch_peak_id: 'bp-1' })

    expect(focusSamplePeak).not.toHaveBeenCalled()
    expect(app.ui.notification.push).toHaveBeenCalledWith(
      expect.objectContaining({
        status: 'warning',
        message: expect.stringContaining('no measured member')
      })
    )
  })

  it('says when the sample opened but its peak list lacks the peak', async () => {
    post.mockResolvedValue([series(['s-1'], [10], ['p1'])])
    focusSamplePeak.mockResolvedValueOnce('missing')
    const jump = useBatchPeakJump()

    await jump.jumpToBrightest({ batch_peak_id: 'bp-1' })

    expect(app.ui.notification.push).toHaveBeenCalledWith(
      expect.objectContaining({
        status: 'warning',
        message: expect.stringContaining('only the sample')
      })
    )
  })

  it('reports a failed read as an error and clears the in-flight mark', async () => {
    post.mockRejectedValue(new Error('boom'))
    const jump = useBatchPeakJump()

    await jump.jumpToBrightest({ batch_peak_id: 'bp-1' })

    expect(app.ui.notification.push).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'error' })
    )
    expect(jump.pendingId).toBeNull()
  })

  it('does nothing without a focused batch or a batch peak', async () => {
    app = makeApp()
    app.data.batch.focusedId = null
    const jump = useBatchPeakJump()
    await jump.jumpToBrightest({ batch_peak_id: 'bp-1' })
    await jump.jumpToBrightest(null)
    expect(post).not.toHaveBeenCalled()
  })
})
