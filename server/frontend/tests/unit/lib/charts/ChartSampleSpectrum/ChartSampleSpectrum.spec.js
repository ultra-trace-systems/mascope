import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// Plotly's own resize, which the chart's exposed one passes on to.
const { plotResize } = vi.hoisted(() => ({ plotResize: vi.fn() }))
// The focused peak and the previewed line, as a test sets them before mounting.
const focus = vi.hoisted(() => ({ peak: null, preview: null }))

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: {
      sample: { focused: { length: 60 } },
      peak: {
        get focused() {
          return focus.peak
        },
        list: [],
        focus: vi.fn()
      }
    },
    ui: { tab: { active: 'sample' }, help: { docUrl: (path = '') => `/docs/${path}` } }
  })
}))
vi.mock('@/lib/panes', () => ({
  usePreview: () => ({
    get peak() {
      return focus.preview
    }
  })
}))
vi.mock('@/lib/toolbars', () => ({ ToolbarIntensityScale: { template: '<span />' } }))
vi.mock('@/lib/utils', () => ({ sampleInstrumentType: () => 'orbitrap' }))
vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: true }))
// The boundaries of a stitched spectrum, as the chart's data gives them; none
// for a spectrum of one range.
const chartData = vi.hoisted(() => ({ shapes: undefined }))
vi.mock('@/lib/charts/ChartSampleSpectrum/data.js', () => ({
  useChartData: () => ({ traces: [], loading: false, shapes: chartData.shapes })
}))
vi.mock('@/lib/charts/BaseChartPlotly.vue', () => ({
  default: {
    props: ['id', 'title', 'data', 'layout', 'config', 'loading'],
    methods: { resize: plotResize },
    template: '<div />'
  }
}))

const { default: ChartSampleSpectrum } =
  await import('@/lib/charts/ChartSampleSpectrum/ChartSampleSpectrum.vue')
const { default: BaseChartPlotly } = await import('@/lib/charts/BaseChartPlotly.vue')

// A splitter divider beside the chart changes its width and not its height,
// which the chart's height watcher never sees: the Sample tab asks it to resize.
describe('ChartSampleSpectrum resize', () => {
  it('passes a resize on to its plot', () => {
    const wrapper = mount(ChartSampleSpectrum, {
      props: { height: 400 },
      global: { directives: { help: {} } }
    })
    plotResize.mockClear()

    wrapper.vm.resize()

    expect(plotResize).toHaveBeenCalledTimes(1)
  })
})

describe('ChartSampleSpectrum boundaries', () => {
  const layoutOf = () =>
    mount(ChartSampleSpectrum, {
      props: { height: 400 },
      global: { directives: { help: {} } }
    })
      .findComponent(BaseChartPlotly)
      .props('layout')

  it('hands the boundaries of a stitched spectrum to the plot', () => {
    chartData.shapes = [{ type: 'line', x0: 67, x1: 67 }]

    expect(layoutOf().shapes).toEqual([{ type: 'line', x0: 67, x1: 67 }])
  })

  it('draws none for a spectrum of one range', () => {
    chartData.shapes = undefined

    expect(layoutOf().shapes).toEqual([])
  })
})

// The view a focused peak zooms to: its m/z window, up to a little over its
// height. A previewed line - a search hit's, or an isotope line the inspector
// found no peak for - moves the window to its own m/z, and the height takes
// in the stick the chart draws for it where that one stands taller.
describe('ChartSampleSpectrum focus', () => {
  const layoutOf = () =>
    mount(ChartSampleSpectrum, {
      props: { height: 400 },
      global: { directives: { help: {} } }
    })
      .findComponent(BaseChartPlotly)
      .props('layout')
  const PEAK = { peak_id: 'p-1', mz: 258.98, height: 800 }

  it("zooms to the focused peak's window", () => {
    focus.peak = PEAK
    focus.preview = null
    const layout = layoutOf()
    expect(layout.xaxis.range[0]).toBeCloseTo(258.93)
    expect(layout.xaxis.range[1]).toBeCloseTo(259.03)
    expect(layout.yaxis.range).toEqual([0, 960])
  })

  it("moves to a previewed line's m/z, tall enough for its expected stick", () => {
    focus.peak = PEAK
    focus.preview = {
      mz: 260.98,
      relative_abundance: 1.95,
      abundance_reference: 1,
      intensity_reference: 800
    }
    const layout = layoutOf()
    expect(layout.xaxis.range[0]).toBeCloseTo(260.93)
    expect(layout.xaxis.range[1]).toBeCloseTo(261.03)
    expect(layout.yaxis.range[1]).toBeCloseTo(800 * 1.95 * 1.2)
  })

  it("keeps the focused peak's height over a shorter previewed line", () => {
    focus.peak = PEAK
    focus.preview = {
      mz: 260.98,
      relative_abundance: 0.1,
      abundance_reference: 1,
      intensity_reference: 800
    }
    expect(layoutOf().yaxis.range).toEqual([0, 960])
  })
})
