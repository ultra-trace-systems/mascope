import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'

// The Sample tab with peak-centric assignment on: the inspector in a column of
// its own, the whole height of the tab, beside the spectrum over the assignment
// time series (or the composition search, while the inspector asks for it).

const { resizeSpectrum, resizeTimeseries } = vi.hoisted(() => ({
  resizeSpectrum: vi.fn(),
  resizeTimeseries: vi.fn()
}))

vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: true }))

vi.mock('@/lib/panes', () => ({
  PaneBrowserPeak: { template: '<div class="peak-browser" />' },
  PanePeakAssign: {
    props: ['showSearch'],
    emits: ['update:showSearch'],
    template: '<div class="inspector" />'
  },
  PanePeakSearch: { props: ['height'], template: '<div class="search" />' }
}))

vi.mock('@/lib/charts', () => ({
  ChartSampleSpectrum: {
    props: ['height'],
    methods: { resize: resizeSpectrum },
    template: '<div class="spectrum" />'
  },
  ChartAssignmentTimeseries: {
    props: ['height'],
    methods: { resize: resizeTimeseries },
    template: '<div class="timeseries" />'
  }
}))

const { ChartSampleSpectrum, ChartAssignmentTimeseries } = await import('@/lib/charts')
const { PanePeakAssign } = await import('@/lib/panes')
const { default: PaneTabSample } = await import('@/lib/panes/PaneTabSample.vue')

// A splitter says which way it splits and where it saves its sizes; a panel
// holds its slot.
const SplitterStub = {
  name: 'SplitterStub',
  props: ['layout', 'stateKey', 'stateStorage'],
  emits: ['resizeend'],
  template:
    '<div class="splitter" :data-layout="layout ?? \'horizontal\'" :data-key="stateKey">' +
    '<slot /></div>'
}
const STUBS = {
  Splitter: SplitterStub,
  SplitterPanel: { props: ['size', 'minSize'], template: '<div class="panel"><slot /></div>' }
}

const mountTab = () => mount(PaneTabSample, { global: { stubs: STUBS } })
// What each panel of a splitter holds, in order.
const held = (splitter) =>
  [...splitter.element.children].map((panel) => panel.firstElementChild?.classList[0])
const splitters = (wrapper) => wrapper.findAllComponents({ name: 'SplitterStub' })
const chartHeights = (wrapper) => [
  wrapper.findComponent(ChartSampleSpectrum).props('height'),
  wrapper.findComponent(ChartAssignmentTimeseries).props('height')
]
// A row's height in pixels: its share of the window less the chrome above the
// tab, worked the way the tab works it.
const rowHeight = (percent) => ((window.innerHeight - 180) * percent) / 100

beforeEach(() => localStorage.clear())
afterEach(() => vi.clearAllMocks())

describe('PaneTabSample with peak assignment', () => {
  it('puts the inspector in a column of its own, beside the spectrum over the time series', () => {
    const [columns, rows] = splitters(mountTab())

    expect(columns.attributes('data-layout')).toBe('horizontal')
    expect(held(columns)).toEqual(['inspector', 'splitter'])
    expect(rows.attributes('data-layout')).toBe('vertical')
    expect(held(rows)).toEqual(['spectrum', 'timeseries'])
  })

  // The rows are the stack the tab has always saved (spectrum over what is
  // below it), so they keep that key and the ratio a user set on it; the
  // column is saved apart.
  it('saves the column apart from the rows', () => {
    const [columns, rows] = splitters(mountTab())

    expect(columns.attributes('data-key')).toBe('sample-tab-assign-columns')
    expect(rows.attributes('data-key')).toBe('sample-tab-assign-split')
  })

  it("sizes the charts from the rows' saved split", () => {
    localStorage.setItem('sample-tab-assign-split', '[60, 40]')

    expect(chartHeights(mountTab())).toEqual([rowHeight(60), rowHeight(40)])
  })

  it('gives the charts new heights when the row divider moves', async () => {
    const wrapper = mountTab()

    splitters(wrapper)[1].vm.$emit('resizeend', { sizes: [70, 30] })
    await wrapper.vm.$nextTick()

    expect(chartHeights(wrapper)).toEqual([rowHeight(70), rowHeight(30)])
  })

  // Moving the column divider changes the charts' width and not their height,
  // which their own `height` watchers would never see.
  it('resizes both charts when the column divider moves', () => {
    const wrapper = mountTab()

    splitters(wrapper)[0].vm.$emit('resizeend', { sizes: [30, 70] })

    expect(resizeSpectrum).toHaveBeenCalledTimes(1)
    expect(resizeTimeseries).toHaveBeenCalledTimes(1)
  })

  it('shows the search in place of the time series while the inspector asks for it', async () => {
    const wrapper = mountTab()

    wrapper.findComponent(PanePeakAssign).vm.$emit('update:showSearch', true)
    await wrapper.vm.$nextTick()

    expect(held(splitters(wrapper)[1])).toEqual(['spectrum', 'search'])
  })
})
