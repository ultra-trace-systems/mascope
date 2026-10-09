import { describe, it, expect, beforeEach, vi } from 'vitest'
import { reactive, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

import { SEGMENT_COLORS } from '@/lib/segments'

// The spectrum chart's traces. A spectrum stitched from several scan ranges is
// drawn run by run, so that no line joins two ranges across a boundary where
// the signal steps, with a line at each boundary; a spectrum of one range is
// the single green trace it has always been.

const get = vi.fn()
vi.mock('@/api', () => ({ api: { http: { get: (...args) => get(...args) } } }))

const app = vi.hoisted(() => ({ current: null }))
vi.mock('@/stores', () => ({ useApp: () => app.current }))
vi.mock('@/lib/panes', () => ({ usePreview: () => ({ peak: null }) }))
vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: false }))

const segments = [
  { index: 0, key: 'reagent', label: 'm/z 40-138', scans: 5, microscans: 1 },
  { index: 1, key: 'low', label: 'm/z 66-124', scans: 3, microscans: 10 }
]
const stitched = {
  mz: [41, 60, 70, 90, 125],
  intensity: [9, 9, 4, 4, 9],
  intensity_unit: 'counts/s',
  segments,
  runs: [
    { segment: 0, mz_lower: 40, mz_upper: 67, from: 0, to: 2 },
    { segment: 1, mz_lower: 67, mz_upper: 122, from: 2, to: 4 },
    { segment: 0, mz_lower: 122, mz_upper: 133, from: 4, to: 5 }
  ]
}
const single = {
  mz: [41, 60, 70],
  intensity: [9, 9, 4],
  intensity_unit: 'counts/s',
  segments: null,
  runs: null
}

let chart

const open = async (spectrum, peak = {}) => {
  app.current = reactive({
    data: {
      sample: { focusedId: null },
      peak: { pending: false, list: [], segments: null, focused: null, ...peak },
      peakAssignment: { peak: { run: null } }
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

const signal = () => chart.traces.filter((trace) => trace.name === 'Signal')

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
})

describe('the signal of a stitched spectrum', () => {
  it('is one trace per run, so that no line crosses a boundary', async () => {
    await open(stitched)

    expect(signal().map((trace) => [...trace.x])).toEqual([[41, 60], [70, 90], [125]])
    expect(signal().map((trace) => [...trace.y])).toEqual([[9, 9], [4, 4], [9]])
  })

  it('draws a segment in one colour wherever it owns a run', async () => {
    await open(stitched)

    expect(signal().map((trace) => trace.line.color)).toEqual([
      SEGMENT_COLORS[0],
      SEGMENT_COLORS[1],
      SEGMENT_COLORS[0]
    ])
  })

  it('names the segment where the signal is hovered', async () => {
    await open(stitched)

    expect(signal()[1].hovertemplate).toContain(
      'segment: <b>m/z 66-124 · 3 scans · 10 microscans</b>'
    )
  })

  it('still dots its samples once zoomed in on them', async () => {
    await open(stitched)

    expect(signal().every((trace) => trace.markSamples)).toBe(true)
  })

  it('has a boundary line where two runs meet', async () => {
    await open(stitched)

    expect(chart.shapes.map((shape) => shape.x0)).toEqual([67, 122])
  })
})

describe('the signal of a spectrum of one range', () => {
  it('is the single green trace it has always been', async () => {
    await open(single)

    expect(signal()).toHaveLength(1)
    expect(signal()[0].line.color).toBe('green')
    expect([...signal()[0].x]).toEqual([41, 60, 70])
    expect(signal()[0].hovertemplate).not.toContain('segment')
    expect(chart.shapes).toEqual([])
  })

  it('is read the same from a server that names no segments at all', async () => {
    const { segments: _segments, runs: _runs, ...older } = single
    await open(older)

    expect(signal()).toHaveLength(1)
    expect(chart.shapes).toEqual([])
  })
})

describe('a peak of a stitched sample', () => {
  const peaks = [
    { peak_id: 'p-1', mz: 62, height: 100, area: 10, segment: 0 },
    { peak_id: 'p-2', mz: 80, height: 14, area: 2, segment: 1 }
  ]

  it('says which segment it was measured in where it is hovered', async () => {
    await open(stitched, { list: peaks, segments })

    const trace = chart.traces.find((candidate) => candidate.name === 'Peak')
    expect(trace.hovertemplate).toContain('segment: <b>%{customdata[4]}</b>')
    expect(trace.customdata[0][4]).toBe('m/z 40-138 · 5 scans · 1 microscan')
    expect(trace.customdata[3][4]).toBe('m/z 66-124 · 3 scans · 10 microscans')
    // The intensities the chart rescales stay where it reads them
    expect(trace.customdata[0].slice(0, 3)).toEqual([100, 10, 62])
  })

  it('names no segment for a sample that has none', async () => {
    const plain = peaks.map((peak) => ({ ...peak, segment: null }))
    await open(single, { list: plain, segments: null })

    const trace = chart.traces.find((candidate) => candidate.name === 'Peak')
    expect(trace.hovertemplate).not.toContain('segment')
  })
})
