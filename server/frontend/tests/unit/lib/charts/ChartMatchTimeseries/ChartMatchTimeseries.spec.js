import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { enableAutoUnmount, mount } from '@vue/test-utils'
import { nextTick } from 'vue'

// The match tab sizes the time series from its splitter, in pixels. The height
// goes on the chart's container and not into the Plotly layout: with
// `responsive` on, Plotly deletes a layout height whenever the plot div changes
// size and autosizes to the container, so a container with no height of its
// own collapses to nothing and the chart with it.

const { app, resize } = vi.hoisted(() => ({ app: {}, resize: vi.fn() }))

vi.mock('@/stores', async () => {
  const { reactive } = await import('vue')
  Object.assign(
    app,
    reactive({
      data: {
        sample: { selected: [{ length: 10 }] },
        match: { visualized: { isotopes: [{ target_isotope_id: 'm0' }] } }
      },
      ui: { split: { right: 50 }, tab: { active: 'match' } }
    })
  )
  return { useApp: () => app }
})

vi.mock('@/lib/toolbars', () => ({ ToolbarIntensityScale: true }))
vi.mock('@/lib/charts/ChartMatchTimeseries/data.js', () => ({
  useChartData: () => ({ traces: [{ name: 'M0', x: [0, 1], y: [1, 2] }] })
}))
// The figure is Plotly's; the stub keeps the layout it was handed and counts
// the resizes asked of it.
vi.mock('@/lib/charts/BaseChartPlotly.vue', () => ({
  default: {
    props: ['id', 'title', 'data', 'layout'],
    methods: { resize, resetZoom: () => {} },
    template: '<div class="plot" />'
  }
}))

const { default: BaseChartPlotly } = await import('@/lib/charts/BaseChartPlotly.vue')
const { default: ChartMatchTimeseries } =
  await import('@/lib/charts/ChartMatchTimeseries/ChartMatchTimeseries.vue')

function mountChart(height) {
  return mount(ChartMatchTimeseries, {
    props: { height, modelValue: { mode: 'average', max: null, log: false } }
  })
}

/** The element the chart fills: the plot stub's parent. */
const container = (wrapper) => wrapper.find('.plot').element.parentElement

enableAutoUnmount(afterEach)

beforeEach(() => {
  resize.mockClear()
  app.ui.tab.active = 'match'
})

describe('ChartMatchTimeseries height', () => {
  it('puts the height on the container and none in the layout', async () => {
    const wrapper = mountChart(185)
    await nextTick()

    expect(container(wrapper).style.height).toBe('185px')
    const layout = wrapper.findComponent(BaseChartPlotly).props('layout')
    expect(layout.yaxis.type).toBe('log')
    expect(layout).not.toHaveProperty('height')
  })

  it('resizes the chart to a new height', async () => {
    const wrapper = mountChart(185)
    await nextTick()

    await wrapper.setProps({ height: 240 })
    await nextTick()

    expect(container(wrapper).style.height).toBe('240px')
    expect(resize).toHaveBeenCalledTimes(1)
  })

  it('resizes the chart once the Match tab shows', async () => {
    mountChart(185)
    app.ui.tab.active = 'sample'
    await nextTick()
    await nextTick()
    expect(resize).not.toHaveBeenCalled()

    app.ui.tab.active = 'match'
    await nextTick()
    await nextTick()
    expect(resize).toHaveBeenCalledTimes(1)
  })
})
