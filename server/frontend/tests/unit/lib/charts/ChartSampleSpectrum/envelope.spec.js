import { describe, it, expect, beforeEach, vi } from 'vitest'
import { reactive, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// The theoretical envelope of the focused peak's assignment: a circle at each
// member's predicted m/z and height, recovered from the row's stored errors.
// A lone M0, whose assignment has no isotopologue row, is drawn too: its one
// circle beside the measured stick is how its m/z error is judged by eye.
// Heights are the members' measured sticks, not their stored intensities,
// which on a TOF are areas and would put the envelope above the trace.
// Rows are shaped as the API sends them: the peak list keys a peak by a
// numeric `peak_id`, an assignment row by its string `sample_peak_id`.
// A line the run expected the spectrum to show and found no peak for (step
// 3.4e) is drawn from the prediction its M0's record carries, as an empty
// circle at its predicted m/z and the M0's height times its share.

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
  sample_peak_id: '7',
  sample_peak_mz: 100.001,
  sample_peak_intensity: 2000,
  mz_error_ppm: 10,
  abundance_error: null
}
const m1 = {
  peak_assignment_id: 'a-1',
  sample_peak_id: '8',
  owner_peak_assignment_id: 'a-0',
  sample_peak_mz: 101.004,
  sample_peak_intensity: 220,
  mz_error_ppm: -5,
  abundance_error: 0.1
}

let chart
// The M0's detail, as the store caches it once the inspector has fetched it.
let details

const open = async (family, peaks = [], focused = m0) => {
  const rows = new Map(family.map((row) => [row.sample_peak_id, row]))
  app.current = reactive({
    data: {
      sample: { focusedId: null },
      peak: {
        pending: false,
        list: peaks,
        segments: null,
        focused: {
          peak_id: Number(focused.sample_peak_id),
          mz: focused.sample_peak_mz,
          height: focused.sample_peak_intensity
        }
      },
      peakAssignment: {
        peak: {
          run: { peak_assignment_run_id: 'r-1' },
          forPeak: (peakId) => rows.get(String(peakId)) ?? null,
          m0Of: () => family[0],
          familyOf: () => family,
          detailOf: (id) => details.get(id) ?? null
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
const notFound = () => chart.traces.filter((trace) => trace.name === 'Not Found Peak')

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  details = new Map()
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

  // A TOF: each stored intensity is the peak's area, 4x its measured height
  const tofPeaks = [
    { peak_id: 7, mz: m0.sample_peak_mz, height: 500, area: 2000 },
    { peak_id: 8, mz: m1.sample_peak_mz, height: 50, area: 220 }
  ]

  it('draws each member at its measured height, not its stored intensity', async () => {
    await open([m0, m1], tofPeaks)

    const [trace] = envelope()
    expect(trace.y[1]).toBeCloseTo(500, 9)
    expect(trace.y[4]).toBeCloseTo(50 / 1.1, 9)
  })

  it("draws a member whose peak is not in the list at the M0's height per intensity", async () => {
    await open([m0, m1], tofPeaks.slice(0, 1))

    const [trace] = envelope()
    expect(trace.y[1]).toBeCloseTo(500, 9)
    expect(trace.y[4]).toBeCloseTo((220 * (500 / 2000)) / 1.1, 9)
  })

  it("borrows the M0's height per intensity when an isotopologue is focused", async () => {
    await open([m0, m1], tofPeaks.slice(0, 1), m1)

    const [trace] = envelope()
    expect(trace.y[4]).toBeCloseTo((220 * (500 / 2000)) / 1.1, 9)
  })
})

describe('the lines the run did not find', () => {
  const record = (lines) => ({ provenance: { predicted_envelope: { lines } } })
  const absent = { line: '81Br', predicted_mz: 101.998, predicted_share: 0.97, state: 'absent' }
  const found = { line: '13C', predicted_mz: 101.004, predicted_share: 0.11, state: 'found' }

  it('draws a line not found as an empty circle at its predicted m/z and height', async () => {
    details.set('a-0', record([found, absent]))
    await open([m0, m1])

    const [trace] = notFound()
    expect(trace.mode).toBe('markers')
    expect(trace.marker.symbol).toBe('circle-open')
    expect(trace.x).toEqual([101.998])
    expect(trace.y[0]).toBeCloseTo(2000 * 0.97, 9)
    expect(trace.customdata[0][3]).toBe('not found')
    // The found line keeps its stick, recovered from its row.
    expect(envelope()).toHaveLength(1)
  })

  it("draws it at the M0's measured height, not its stored intensity", async () => {
    details.set('a-0', record([absent]))
    await open([m0], [{ peak_id: 7, mz: m0.sample_peak_mz, height: 500, area: 2000 }])

    expect(notFound()[0].y[0]).toBeCloseTo(500 * 0.97, 9)
  })

  it('names a line only an artifact sat on as not tested', async () => {
    details.set('a-0', record([{ ...absent, state: 'untestable', untestable: 'artifact' }]))
    await open([m0])

    expect(notFound()[0].customdata[0][3]).toBe('not tested')
  })

  it("reads the M0's record when one of its isotopologues is focused", async () => {
    details.set('a-0', record([absent]))
    details.set('a-1', record([{ ...absent, predicted_mz: 103.0 }]))
    await open([m0, m1], [], m1)

    expect(notFound()[0].x).toEqual([101.998])
  })

  it('draws no line of an M0 with no height to scale it by', async () => {
    details.set('a-0', record([absent]))
    await open([{ ...m0, sample_peak_intensity: null }, m1], [], m1)

    expect(notFound()).toHaveLength(0)
  })

  it('draws nothing more where the run asked the spectrum nothing', async () => {
    details.set('a-0', record([found]))
    await open([m0, m1])
    expect(notFound()).toHaveLength(0)

    details.clear()
    await open([m0, m1])
    expect(notFound()).toHaveLength(0)
    expect(envelope()).toHaveLength(1)
  })
})
