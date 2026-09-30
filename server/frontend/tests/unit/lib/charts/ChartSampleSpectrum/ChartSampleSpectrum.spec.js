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
vi.mock('@/lib/charts/ChartSampleSpectrum/data.js', () => ({
  useChartData: () => ({ traces: [], loading: false })
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
