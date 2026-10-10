import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// Plotly's own resize, which the chart's exposed one passes on to.
const { plotResize } = vi.hoisted(() => ({ plotResize: vi.fn() }))

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: {
      sample: { focused: { length: 60 } },
      peak: { focused: null, list: [], focus: vi.fn() }
    },
    ui: { tab: { active: 'sample' }, help: { docUrl: (path = '') => `/docs/${path}` } }
  })
}))
vi.mock('@/lib/panes', () => ({ usePreview: () => ({ peak: null }) }))
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
