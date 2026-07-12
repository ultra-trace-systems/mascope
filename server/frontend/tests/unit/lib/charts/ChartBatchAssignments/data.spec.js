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

/** Record as returned by POST /batch-peaks/records/series */
const peakRecord = (id, series, extra = {}) => ({
  batch_peak_id: id,
  mz: 181.0707,
  consensus_formula: 'C6H12O6',
  consensus_ion_formula: 'C6H13O6+',
  ionization_mechanism_id: 'mech-1',
  consensus_tier: 'identified',
  best_fit_score: 0.95,
  support_fraction: 1.0,
  n_present: series.sample_item_ids.length,
  is_ambiguous: false,
  peak_series: series,
  ...extra
})

describe('chart.batch.assignments data store', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mockApp.data.batch.focusedId = 'batch-1'
    store = useChartAssignmentsData()
  })

  afterEach(() => {
    store.$dispose()
  })

  it('requests batch-peak series scoped by batch id with the occupancy filter', async () => {
    api.http.post.mockResolvedValue([])
    await store.load()

    expect(api.http.post).toHaveBeenCalledTimes(1)
    const [url, body] = api.http.post.mock.calls[0]
    expect(url).toBe('/batch-peaks/records/series')
    expect(body).toEqual({ sample_batch_id: 'batch-1', min_n_present: 2 })
  })

  it('builds one trace per batch peak plus TIC, with y aligned to samples', async () => {
    api.http.post.mockResolvedValue([
      peakRecord('bp1', {
        sample_item_ids: ['s1', 's3'],
        intensities: [5, 7],
        tiers: ['identified', 'identified']
      })
    ])
    await store.load()
    await nextTick()

    expect(store.traces).toHaveLength(2) // bp1 + TIC
    const [peak, tic] = store.traces
    expect(peak.y).toEqual([5, null, 7]) // s2 absent -> null
    expect(peak.assignmentData.batch_peak_id).toBe('bp1')
    expect(peak.marker.symbol).toBe('square') // identified
    expect(peak.name).toContain('C6H12O6')
    expect(tic.name).toBe('TIC')
    expect(tic.y).toEqual([1000, 2000, 3000])
  })

  it('labels an unassigned batch peak by m/z and marks it open', async () => {
    api.http.post.mockResolvedValue([
      peakRecord(
        'bp2',
        { sample_item_ids: ['s2'], intensities: [3], tiers: ['unassigned'] },
        { consensus_formula: null, consensus_tier: 'unassigned' }
      )
    ])
    await store.load()
    await nextTick()

    const [peak] = store.traces
    expect(peak.name).toContain('m/z')
    expect(peak.marker.symbol).toBe('circle-open')
  })

  it('shares the per-sample customdata and text arrays across peak traces', async () => {
    api.http.post.mockResolvedValue([
      peakRecord('bp1', { sample_item_ids: ['s1'], intensities: [5], tiers: ['identified'] }),
      peakRecord(
        'bp2',
        { sample_item_ids: ['s2'], intensities: [6], tiers: ['candidate'] },
        { consensus_tier: 'candidate' }
      )
    ])
    await store.load()
    await nextTick()

    const [a, b] = store.traces
    expect(a.customdata).toBe(b.customdata)
    expect(a.text).toBe(b.text)
    expect(a.y).not.toBe(b.y)
    expect(b.marker.symbol).toBe('square-open') // candidate
  })
})
