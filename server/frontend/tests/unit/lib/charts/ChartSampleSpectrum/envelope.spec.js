import { describe, it, expect, beforeEach, vi } from 'vitest'
import { reactive, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// The theoretical envelope of the focused peak's assignment: a circle at each
// member's predicted m/z and height, recovered from the row's stored errors.
// A lone M0, whose assignment has no isotopologue row, is drawn too: its one
// circle beside the measured stick is how its m/z error is judged by eye.

const get = vi.fn()
vi.mock('@/api', () => ({ api: { http: { get: (...args) => get(...args) } } }))

const app = vi.hoisted(() => ({ current: null }))
vi.mock('@/stores', () => ({ useApp: () => app.current }))
vi.mock('@/lib/panes', () => ({ usePreview: () => ({ peak: null }) }))
vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: true }))

const spectrum = {
  mz: [100, 101, 102],
  intensity: [0, 0, 0],
  intensity_unit: 'counts/s',
  segments: null,
  runs: null
}

const m0 = {
  peak_assignment_id: 'a-0',
  sample_peak_id: 'p-0',
  sample_peak_mz: 100.001,
  sample_peak_intensity: 2000,
  mz_error_ppm: 10,
  abundance_error: null
}
const m1 = {
  peak_assignment_id: 'a-1',
  sample_peak_id: 'p-1',
  owner_peak_assignment_id: 'a-0',
  sample_peak_mz: 101.004,
  sample_peak_intensity: 220,
  mz_error_ppm: -5,
  abundance_error: 0.1
}

let chart

const open = async (family) => {
  const rows = new Map(family.map((row) => [row.sample_peak_id, row]))
  app.current = reactive({
    data: {
      sample: { focusedId: null },
      peak: {
        pending: false,
        list: [],
        segments: null,
        focused: { peak_id: 'p-0', mz: m0.sample_peak_mz, height: m0.sample_peak_intensity }
      },
      peakAssignment: {
        peak: {
          run: { peak_assignment_run_id: 'r-1' },
          forPeak: (peakId) => rows.get(peakId) ?? null,
          familyOf: () => family
        }
      }
    },
    ui: { chart: { register: vi.fn() } }
  })
  get.mockResolvedValue(spectrum)
  const { useChartData } = await import('@/lib/charts/ChartSampleSpectrum/data.js')
  chart = useChartData()
  app.current.data.sample.focusedId = 's-1'
  await vi.waitFor(() => expect(chart.loading).toBe(false))
  await nextTick()
}

const envelope = () => chart.traces.filter((trace) => trace.name === 'Theoretical Peak')

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
})

describe('the theoretical envelope of the focused peak', () => {
  it('draws a lone M0 as one circle at its predicted m/z and height', async () => {
    await open([m0])

    expect(envelope()).toHaveLength(1)
    const [trace] = envelope()
    expect(trace.x).toHaveLength(3)
    expect(trace.x[0]).toBeCloseTo(100.001 / (1 + 10 / 1e6), 9)
    expect(trace.x[1]).toBe(trace.x[0])
    expect(trace.y).toEqual([0, 2000, null])
  })

  it('draws one circle per member of a family', async () => {
    await open([m0, m1])

    const [trace] = envelope()
    expect(trace.x.filter((x) => x != null)).toHaveLength(4)
    expect(trace.x[3]).toBeCloseTo(101.004 / (1 - 5 / 1e6), 9)
    expect(trace.y[4]).toBeCloseTo(220 / 1.1, 9)
  })
})
