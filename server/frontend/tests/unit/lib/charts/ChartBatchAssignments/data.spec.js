import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { nextTick, reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// The store talks to the API and the app store at setup; both are stubbed.
vi.mock('@/api', () => ({
  api: {
    socket: { on: vi.fn(), off: vi.fn(), id: 'test-sid' },
    http: { get: vi.fn(), post: vi.fn() }
  }
}))

const mockApp = reactive({
  ui: { darkmode: { active: false } },
  data: {
    batch: { focusedId: 'batch-1' },
    batchPeak: { selectedIds: [] }, // the ledger selection that drives the chart
    sample: {
      list: [
        {
          sample_item_id: 's1',
          sample_item_name: 'Sample 1',
          datetime: '2026-01-01T10:00:00',
          tic: 1000,
          length: 2
        },
        {
          sample_item_id: 's2',
          sample_item_name: 'Sample 2',
          datetime: '2026-01-01T11:00:00',
          tic: 2000,
          length: 2
        },
        {
          sample_item_id: 's3',
          sample_item_name: 'Sample 3',
          datetime: '2026-01-01T12:00:00',
          tic: 3000,
          length: 2
        }
      ]
    }
  }
})

vi.mock('@/stores', () => ({ useApp: () => mockApp }))

import { api } from '@/api'
import { useChartAssignmentsData } from '@/lib/charts/ChartBatchAssignments/data'
import { MAX_SELECTED_BATCH_PEAKS } from '@/stores/data/modules/batchPeak/ledger'

/**
 * Record as returned by POST /batch-peaks/records/series (series included).
 * `series` carries the parallel arrays sample_item_ids / sample_peak_ids /
 * intensities / tiers; sample_peak_ids is omitted by some fixtures on purpose.
 */
const peakRecord = (id, series, extra = {}) => ({
  batch_peak_id: id,
  mz: 181.0707,
  consensus_formula: 'C6H12O6',
  consensus_tier: 'assigned',
  n_present: series.sample_item_ids.length,
  peak_series: series,
  ...extra
})

describe('chart.batch.assignments data store (selection-driven)', () => {
  let store

  const flushAsync = async () => {
    await nextTick()
    await vi.waitFor(() => expect(store.pending).toBe(false))
    await nextTick()
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mockApp.data.batchPeak.selectedIds = []
    store = useChartAssignmentsData()
  })

  afterEach(() => {
    store.$dispose()
  })

  it('fetches series only for the SELECTED batch peaks, scoped by batch id', async () => {
    api.http.post.mockResolvedValue([
      peakRecord('bp1', {
        sample_item_ids: ['s1', 's3'],
        intensities: [5, 7],
        tiers: ['assigned', 'assigned']
      })
    ])

    mockApp.data.batchPeak.selectedIds = ['bp1']
    await flushAsync()

    expect(api.http.post).toHaveBeenCalledTimes(1)
    const [url, body] = api.http.post.mock.calls[0]
    expect(url).toBe('/batch-peaks/records/series')
    expect(body).toEqual({ sample_batch_id: 'batch-1', batch_peak_ids: ['bp1'] })

    expect(store.traces).toHaveLength(2) // bp1 + TIC
    const [peak, tic] = store.traces
    expect(peak.y).toEqual([5, null, 7]) // s2 absent -> null
    expect(peak.assignmentData.batch_peak_id).toBe('bp1')
    expect(peak.marker.symbol).toBe('square') // assigned
    expect(tic.name).toBe('TIC')
  })

  it('fans sample_peak_ids onto the sample axis, aligned with y', async () => {
    api.http.post.mockResolvedValue([
      peakRecord('bp1', {
        sample_item_ids: ['s3', 's1'], // deliberately out of sample-list order
        sample_peak_ids: ['p3', 'p1'],
        intensities: [7, 5],
        tiers: ['assigned', 'assigned']
      })
    ])

    mockApp.data.batchPeak.selectedIds = ['bp1']
    await flushAsync()

    const [peak] = store.traces
    // Same axis as y: index 1 is sample s2, where the batch peak is absent.
    expect(peak.y).toEqual([5, null, 7])
    expect(peak.assignmentData.sample_peak_ids).toEqual(['p1', null, 'p3'])
  })

  it('resolves the clicked point to its sample peak, and to null where there is none', async () => {
    api.http.post.mockResolvedValue([
      peakRecord('bp1', {
        sample_item_ids: ['s1', 's3'],
        sample_peak_ids: ['p1', 'p3'],
        intensities: [5, 7],
        tiers: ['assigned', 'assigned']
      })
    ])

    mockApp.data.batchPeak.selectedIds = ['bp1']
    await flushAsync()

    expect(store.samplePeakIdAt(0, 0)).toBe('p1')
    expect(store.samplePeakIdAt(0, 2)).toBe('p3')
    // The batch peak is absent in s2 -> the click degrades to sample focus only.
    expect(store.samplePeakIdAt(0, 1)).toBeNull()
    // The TIC reference (last trace) is not a batch peak.
    expect(store.samplePeakIdAt(1, 0)).toBeNull()
    // Out-of-range point/curve indices resolve rather than throw.
    expect(store.samplePeakIdAt(9, 0)).toBeNull()
    expect(store.samplePeakIdAt(0, 9)).toBeNull()
  })

  it('tolerates a series without sample_peak_ids (server predating the field)', async () => {
    api.http.post.mockResolvedValue([
      peakRecord('bp1', {
        sample_item_ids: ['s1'],
        intensities: [5],
        tiers: ['assigned']
      })
    ])

    mockApp.data.batchPeak.selectedIds = ['bp1']
    await flushAsync()

    expect(store.traces[0].assignmentData.sample_peak_ids).toEqual([null, null, null])
    expect(store.samplePeakIdAt(0, 0)).toBeNull()
  })

  it('adds only newly-selected peaks and drops de-selected ones', async () => {
    api.http.post.mockResolvedValueOnce([
      peakRecord('bp1', { sample_item_ids: ['s1'], intensities: [5], tiers: ['assigned'] })
    ])
    mockApp.data.batchPeak.selectedIds = ['bp1']
    await flushAsync()
    expect(store.traces).toHaveLength(2) // bp1 + TIC

    // Select bp2 in addition: fetch is made ONLY for the newly-selected id.
    api.http.post.mockResolvedValueOnce([
      peakRecord(
        'bp2',
        { sample_item_ids: ['s2'], intensities: [6], tiers: ['candidate'] },
        { consensus_tier: 'candidate' }
      )
    ])
    mockApp.data.batchPeak.selectedIds = ['bp1', 'bp2']
    await flushAsync()
    expect(api.http.post).toHaveBeenLastCalledWith(
      '/batch-peaks/records/series',
      { sample_batch_id: 'batch-1', batch_peak_ids: ['bp2'] },
      expect.anything()
    )
    expect(store.traces).toHaveLength(3) // bp1 + bp2 + TIC

    // De-select bp1: no fetch, just drop it.
    api.http.post.mockClear()
    mockApp.data.batchPeak.selectedIds = ['bp2']
    await flushAsync()
    expect(api.http.post).not.toHaveBeenCalled()
    expect(store.traces).toHaveLength(2) // bp2 + TIC
    expect(store.traces[0].assignmentData.batch_peak_id).toBe('bp2')
  })

  it('plots nothing (only the TIC reference) when the selection is empty', async () => {
    await flushAsync()
    expect(api.http.post).not.toHaveBeenCalled()
    // samples exist, so the always-on TIC trace is present, but no batch-peak traces.
    expect(store.traces).toHaveLength(1)
    expect(store.traces[0].name).toBe('TIC')
  })

  it('refetches every plotted series, naming the run, when the ledger switches runs', async () => {
    api.http.post.mockResolvedValue([
      peakRecord('bp1', { sample_item_ids: ['s1'], intensities: [5], tiers: ['assigned'] })
    ])
    mockApp.data.batchPeak.selectedIds = ['bp1']
    await flushAsync()
    // The live ledger names no run, so the request is exactly what it was.
    expect(api.http.post.mock.calls[0][1]).toEqual({
      sample_batch_id: 'batch-1',
      batch_peak_ids: ['bp1']
    })

    // An earlier run on screen: the same selection, read from that run's snapshot.
    api.http.post.mockClear()
    mockApp.data.batchPeakRun = { viewingId: 'run-1' }
    await flushAsync()
    expect(api.http.post).toHaveBeenCalledTimes(1)
    expect(api.http.post.mock.calls[0][1]).toEqual({
      sample_batch_id: 'batch-1',
      batch_peak_ids: ['bp1'],
      batch_peak_run_id: 'run-1'
    })
    expect(store.traces).toHaveLength(2) // bp1 + TIC, not bp1 twice

    // Back to the current run: refetched again, without a run id.
    api.http.post.mockClear()
    mockApp.data.batchPeakRun = { viewingId: null }
    await flushAsync()
    expect(api.http.post.mock.calls[0][1]).toEqual({
      sample_batch_id: 'batch-1',
      batch_peak_ids: ['bp1']
    })
  })
})

// The ledger is unbounded on purpose, so "select all" over a large batch is a
// selection with no natural size. These cover what stops that from becoming an
// unbounded amount of work: the cap, the request count it implies, and the
// discipline that keeps two overlapping selections out of one plot. The ledger
// pane caps its own selection (see paneBrowserBatchPeaks.spec.js); this is the
// store holding the same line on the way in, which it has to, because the
// socket handler reads the selection without going through the table at all.
describe('chart.batch.assignments data store (bounded selection)', () => {
  let store

  const flushAsync = async () => {
    await nextTick()
    await vi.waitFor(() => expect(store.pending).toBe(false))
    await nextTick()
  }

  const SERIES = { sample_item_ids: ['s1'], intensities: [5], tiers: ['assigned'] }

  /** Ids as the ledger would hand them over: display order, `n` of them. */
  const ids = (n) => Array.from({ length: n }, (_, i) => `bp-${i}`)

  /** Answer each request with a record per id it asked for. */
  const respondWithRequested = () => {
    api.http.post.mockReset()
    api.http.post.mockImplementation((url, body) =>
      Promise.resolve(body.batch_peak_ids.map((id) => peakRecord(id, SERIES)))
    )
  }

  /**
   * Make every request hang until the test answers it, so responses can be
   * settled out of order. Returns the deferred handles, in call order.
   */
  const gateRequests = () => {
    const gates = []
    api.http.post.mockReset()
    api.http.post.mockImplementation(
      () => new Promise((resolve, reject) => gates.push({ resolve, reject }))
    )
    return gates
  }

  /** The ids one gated request asked for. */
  const requestedBy = (call) => api.http.post.mock.calls[call][1].batch_peak_ids

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mockApp.data.batchPeak.selectedIds = []
    // Restored explicitly: one case below switches batch, and the mockApp is
    // shared with the describe above.
    mockApp.data.batch.focusedId = 'batch-1'
    store = useChartAssignmentsData()
  })

  afterEach(() => {
    store.$dispose()
  })

  it('plots the cap and no more, in a bounded number of requests', async () => {
    respondWithRequested()
    const selected = ids(MAX_SELECTED_BATCH_PEAKS + 50)

    mockApp.data.batchPeak.selectedIds = selected
    await flushAsync()

    // ceil(cap / 100 per request) - not one request per hundred SELECTED peaks.
    expect(api.http.post).toHaveBeenCalledTimes(Math.ceil(MAX_SELECTED_BATCH_PEAKS / 100))
    const requested = api.http.post.mock.calls.flatMap(([, body]) => body.batch_peak_ids)
    expect(requested).toEqual(selected.slice(0, MAX_SELECTED_BATCH_PEAKS))

    expect(store.selectedCount).toBe(selected.length)
    expect(store.plottedCount).toBe(MAX_SELECTED_BATCH_PEAKS)
    expect(store.truncated).toBe(true)
    expect(store.traces).toHaveLength(MAX_SELECTED_BATCH_PEAKS + 1) // + TIC
  })

  it('reports a selection that fits as untruncated', async () => {
    respondWithRequested()

    mockApp.data.batchPeak.selectedIds = ids(120)
    await flushAsync()

    expect(api.http.post).toHaveBeenCalledTimes(2) // 100 + 20
    expect(store.truncated).toBe(false)
    expect(store.plottedCount).toBe(120)
    expect(store.traces).toHaveLength(121)
  })

  it('asks for a cancellable request the interceptor will not announce', async () => {
    respondWithRequested()

    mockApp.data.batchPeak.selectedIds = ['bp1']
    await flushAsync()

    const [, , config] = api.http.post.mock.calls[0]
    // A cancelled request reaches the response interceptor with no response at
    // all, which it would otherwise report to the user as a timeout.
    expect(config.errors).toBe('inline')
    expect(config.signal).toBeInstanceOf(AbortSignal)
  })

  it('aborts the requests of a selection that was superseded mid-flight', async () => {
    const gates = gateRequests()

    mockApp.data.batchPeak.selectedIds = ids(150) // two requests
    await nextTick()
    expect(gates).toHaveLength(2)
    const abandoned = api.http.post.mock.calls.map(([, , config]) => config.signal)
    expect(abandoned.every((signal) => signal.aborted)).toBe(false)

    // The user selects something else before any of it comes back.
    mockApp.data.batchPeak.selectedIds = ['bp-late']
    await nextTick()

    expect(abandoned.every((signal) => signal.aborted)).toBe(true)
    expect(gates).toHaveLength(3) // one new request, not a re-issue of the old set
    expect(requestedBy(2)).toEqual(['bp-late'])
  })

  it('keeps a superseded response out of the plot even when it answers last', async () => {
    const gates = gateRequests()

    mockApp.data.batchPeak.selectedIds = ['bp-old']
    await nextTick()
    mockApp.data.batchPeak.selectedIds = ['bp-new']
    await nextTick()

    // The current selection answers first, the abandoned one after it.
    gates[1].resolve([peakRecord('bp-new', SERIES)])
    gates[0].resolve([peakRecord('bp-old', SERIES)])
    await flushAsync()

    // One batch peak plus the TIC: the stale record is dropped, not appended,
    // so the plot never shows two selections at once.
    expect(store.traces).toHaveLength(2)
    expect(store.traces[0].assignmentData.batch_peak_id).toBe('bp-new')
  })

  it('orders traces by the selection, not by what was already held', async () => {
    // Select-all, Ctrl+A and a shift-click range all REPLACE the selection with
    // rows in display order, so a newly-fetched peak can belong ABOVE one
    // already plotted. Appending what arrives to what is held would put it
    // after, which reshuffles the colours of everything below it.
    respondWithRequested()
    mockApp.data.batchPeak.selectedIds = ['bp-b']
    await flushAsync()

    mockApp.data.batchPeak.selectedIds = ['bp-a', 'bp-b', 'bp-c']
    await flushAsync()

    const plotted = store.traces.slice(0, -1).map((trace) => trace.assignmentData.batch_peak_id)
    expect(plotted).toEqual(['bp-a', 'bp-b', 'bp-c'])
  })

  it('does not report the abort that superseding a selection causes', async () => {
    const gates = gateRequests()

    mockApp.data.batchPeak.selectedIds = ['bp-old']
    await nextTick()
    mockApp.data.batchPeak.selectedIds = ['bp-new']
    await nextTick()

    // Aborting is what fails the abandoned request, and axios rejects it with a
    // response-less error indistinguishable from a network failure. Reporting
    // that would put a failure notice on a chart that is loading normally.
    gates[0].reject(Object.assign(new Error('canceled'), { code: 'ERR_CANCELED' }))
    gates[1].resolve([peakRecord('bp-new', SERIES)])
    await flushAsync()

    expect(store.error).toBeNull()
    expect(store.traces).toHaveLength(2) // bp-new + TIC
  })

  it('still re-reads after a fold-in whose refetch was superseded mid-flight', async () => {
    respondWithRequested()
    mockApp.data.batchPeak.selectedIds = ['bp1']
    await flushAsync()
    expect(store.traces[0].name).toContain('C6H12O6')

    const [, reload] = api.socket.on.mock.calls.find(
      ([event]) => event === 'peak_assignment_reload'
    )
    // The fold-in changed this batch peak's consensus.
    api.http.post.mockReset()
    api.http.post.mockImplementation((url, body) =>
      Promise.resolve(
        body.batch_peak_ids.map((id) => peakRecord(id, SERIES, { consensus_formula: 'C9H12N2' }))
      )
    )

    // The fold-in event and the ledger's own reload of that same event arrive
    // together, and the ledger republishes an equal selection - which supersedes
    // the re-read. A run that only diffs ids finds every wanted peak already
    // held, returns early, and leaves the pre-fold-in consensus plotted for good.
    reload()
    mockApp.data.batchPeak.selectedIds = ['bp1']
    await flushAsync()

    expect(store.traces[0].name).toContain('C9H12N2')
  })

  it('abandons a batch that was switched away from while its series were in flight', async () => {
    const gates = gateRequests()
    mockApp.data.batchPeak.selectedIds = ['bp-old']
    await nextTick()
    const { signal } = api.http.post.mock.calls[0][2]

    mockApp.data.batch.focusedId = 'batch-2'
    await nextTick()

    expect(signal.aborted).toBe(true)
    expect(store.pending).toBe(false)
    expect(store.traces).toHaveLength(1) // TIC only; the old batch's plot is gone

    // The abandoned response cannot repopulate the new batch's chart.
    gates[0].resolve([peakRecord('bp-old', SERIES)])
    await flushAsync()
    expect(store.traces).toHaveLength(1)

    mockApp.data.batch.focusedId = 'batch-1'
    await nextTick()
  })

  it('refetches on peak_assignment_reload under the cap, without blanking the plot', async () => {
    respondWithRequested()
    mockApp.data.batchPeak.selectedIds = ids(MAX_SELECTED_BATCH_PEAKS + 50)
    await flushAsync()
    const plotted = store.traces.length

    // The handler the store registered for the arrival fold-in / backfill.
    const [, reload] = api.socket.on.mock.calls.find(
      ([event]) => event === 'peak_assignment_reload'
    )
    api.http.post.mockClear()
    reload()

    // Still drawn while the new consensus is on its way: the reload used to
    // empty the plot first and refill it request by request.
    expect(store.traces).toHaveLength(plotted)
    await flushAsync()

    expect(api.http.post).toHaveBeenCalledTimes(Math.ceil(MAX_SELECTED_BATCH_PEAKS / 100))
    expect(store.traces).toHaveLength(plotted)
  })

  it('keeps the last good series up when a fold-in refresh fails', async () => {
    respondWithRequested()
    mockApp.data.batchPeak.selectedIds = ['bp1']
    await flushAsync()
    expect(store.traces).toHaveLength(2) // bp1 + TIC

    const [, reload] = api.socket.on.mock.calls.find(
      ([event]) => event === 'peak_assignment_reload'
    )
    api.http.post.mockReset()
    api.http.post.mockRejectedValue({ response: { data: { error: 'series unavailable' } } })
    reload()
    await flushAsync()

    // Emptying the chart because a REFRESH failed throws away series that are
    // merely out of date, which is worse than showing them beside the message
    // saying the refresh did not happen.
    expect(store.error).toBe('series unavailable')
    expect(store.traces).toHaveLength(2)
  })

  it('surfaces a failed series load instead of leaving the chart silently empty', async () => {
    api.http.post.mockReset()
    api.http.post.mockRejectedValue({ response: { data: { error: 'series unavailable' } } })

    mockApp.data.batchPeak.selectedIds = ['bp1']
    await flushAsync()

    expect(store.error).toBe('series unavailable')
    expect(store.traces).toHaveLength(1) // TIC only

    // Clearing the selection clears the failure with it.
    respondWithRequested()
    mockApp.data.batchPeak.selectedIds = []
    await flushAsync()
    expect(store.error).toBeNull()
  })
})
