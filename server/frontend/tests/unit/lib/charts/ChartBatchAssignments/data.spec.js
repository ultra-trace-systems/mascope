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
        { sample_item_id: 's1', sample_item_name: 'Sample 1', datetime: '2026-01-01T10:00:00', tic: 1000, length: 2 },
        { sample_item_id: 's2', sample_item_name: 'Sample 2', datetime: '2026-01-01T11:00:00', tic: 2000, length: 2 },
        { sample_item_id: 's3', sample_item_name: 'Sample 3', datetime: '2026-01-01T12:00:00', tic: 3000, length: 2 }
      ]
    }
  }
})

vi.mock('@/stores', () => ({ useApp: () => mockApp }))

import { api } from '@/api'
import { useChartAssignmentsData } from '@/lib/charts/ChartBatchAssignments/data'

/** Record as returned by POST /batch-peaks/records/series (series included) */
const peakRecord = (id, series, extra = {}) => ({
  batch_peak_id: id,
  mz: 181.0707,
  consensus_formula: 'C6H12O6',
  consensus_tier: 'identified',
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
        tiers: ['identified', 'identified']
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
    expect(peak.marker.symbol).toBe('square') // identified
    expect(tic.name).toBe('TIC')
  })

  it('adds only newly-selected peaks and drops de-selected ones', async () => {
    api.http.post.mockResolvedValueOnce([
      peakRecord('bp1', { sample_item_ids: ['s1'], intensities: [5], tiers: ['identified'] })
    ])
    mockApp.data.batchPeak.selectedIds = ['bp1']
    await flushAsync()
    expect(store.traces).toHaveLength(2) // bp1 + TIC

    // Select bp2 in addition: fetch is made ONLY for the newly-selected id.
    api.http.post.mockResolvedValueOnce([
      peakRecord('bp2', { sample_item_ids: ['s2'], intensities: [6], tiers: ['candidate'] }, { consensus_tier: 'candidate' })
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
})
