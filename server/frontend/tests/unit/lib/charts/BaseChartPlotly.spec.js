import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// Plotly is stubbed down to the calls BaseChartPlotly makes. A draw leaves two
// things on the graph div that matter here: Plotly's event hook, and the full
// layout, whose x range and plot-area width say how far apart a trace's samples
// land on screen. The cases set the full layout themselves and fire the event
// Plotly would.
const { handlers } = vi.hoisted(() => ({ handlers: {} }))

vi.mock('plotly.js-dist-min', () => ({
  default: {
    newPlot: vi.fn((gd) => {
      gd.on = (event, handler) => {
        handlers[event] = handler
      }
    }),
    react: vi.fn(),
    relayout: vi.fn(),
    update: vi.fn(),
    restyle: vi.fn(),
    Plots: { resize: vi.fn() }
  }
}))
// Reactive, so a case can take the right split away and give it back: the
// chart redraws from the traces it already has, as after a pane change.
vi.mock('@/stores', async () => {
  const { reactive } = await import('vue')
  const app = reactive({ ui: { split: { right: 50 } } })
  return { useApp: () => app }
})

const { default: Plotly } = await import('plotly.js-dist-min')
const { useApp } = await import('@/stores')
const { default: BaseChartPlotly } = await import('@/lib/charts/BaseChartPlotly.vue')

/** A measured profile: eleven samples one unit apart, zero at either end. */
const signal = () => ({
  name: 'Signal',
  mode: 'lines',
  markSamples: true,
  line: { color: 'green' },
  x: Float32Array.from({ length: 11 }, (_, i) => i),
  y: Float32Array.from({ length: 11 }, (_, i) => (i === 0 || i === 10 ? 0 : 100))
})

/** A peak marker, which does not ask for its points to be dotted. */
const peak = () => ({ name: 'Peak', mode: 'lines', x: [5, 5], y: [0, 100] })

async function mountChart(data, props = {}) {
  const wrapper = mount(BaseChartPlotly, {
    props: {
      id: 'chart',
      title: 'Chart',
      data,
      layout: { xaxis: { autorange: true }, yaxis: { autorange: true } },
      ...props
    },
    global: { directives: { tooltip: {} } }
  })
  await flushPromises()
  return wrapper
}

/** Plotly having drawn the x axis over `range` across `width` px. */
async function drawn(wrapper, range, width, event = 'plotly_afterplot') {
  wrapper.find('.plot').element._fullLayout = { xaxis: { range }, _size: { w: width } }
  handlers[event]({})
  await flushPromises()
}

/** The traces Plotly was handed last. */
const lastDrawn = () => Plotly.react.mock.calls.at(-1)[1]

/** The layout Plotly was handed last. */
const lastLayout = () => Plotly.react.mock.calls.at(-1)[2]

beforeEach(() => {
  vi.clearAllMocks()
})

describe('BaseChartPlotly sample dots', () => {
  it('draws a profile as a line until the view is known', async () => {
    await mountChart([signal()])

    const [first] = Plotly.newPlot.mock.calls[0][1]
    expect(first.mode).toBe('lines')
    expect(first.marker).toBeUndefined()
  })

  // Ten one-unit gaps across 100 px are 10 px apart; across 50 px, 5 px.
  it('dots the samples once a zoom spreads them far enough apart', async () => {
    const wrapper = await mountChart([signal()])

    await drawn(wrapper, [0, 10], 100)
    expect(lastDrawn()[0].mode).toBe('lines+markers')

    await drawn(wrapper, [0, 100], 100, 'plotly_relayout')
    expect(lastDrawn()[0].mode).toBe('lines')
  })

  it('never hands Plotly the flag that asks for dots', async () => {
    const wrapper = await mountChart([signal()])
    await drawn(wrapper, [0, 10], 100)

    expect(Plotly.newPlot.mock.calls[0][1][0]).not.toHaveProperty('markSamples')
    expect(lastDrawn()[0]).not.toHaveProperty('markSamples')
  })

  it('hands Plotly a trace that does not ask for dots as it was given', async () => {
    const marker = peak()
    const wrapper = await mountChart([signal(), marker])

    await drawn(wrapper, [0, 10], 100)
    expect(lastDrawn()[1]).toEqual(marker)
  })

  it('redraws nothing for a zoom that changes no dots', async () => {
    const wrapper = await mountChart([signal()])
    await drawn(wrapper, [0, 10], 100)
    const draws = Plotly.react.mock.calls.length

    await drawn(wrapper, [0, 9], 100, 'plotly_relayout')
    expect(Plotly.react.mock.calls.length).toBe(draws)
  })

  // Adding dots can widen an autoranged axis, which narrows the gaps a little:
  // the dots stay down to a smaller gap than the one that brought them.
  it('keeps the dots it has for a gap that would not have added them', async () => {
    const between = 55 // 5.5 px gaps, between the two thresholds
    const kept = await mountChart([signal()])
    await drawn(kept, [0, 10], 100)
    await drawn(kept, [0, 10], between)
    expect(lastDrawn()[0].mode).toBe('lines+markers')

    const fresh = await mountChart([signal()])
    await drawn(fresh, [0, 10], between)
    expect(lastDrawn()[0].mode).toBe('lines')
  })
})

describe('BaseChartPlotly selection', () => {
  /** What each of `traces` has selected. */
  const selectedOf = (traces) => traces.map((trace) => trace.selectedpoints)

  /** The trace and layout changes of the last update, the selection's draw. */
  const lastUpdate = () => Plotly.update.mock.calls.at(-1).slice(1)

  it('draws the selection from the first draw', async () => {
    await mountChart([peak(), peak()], { selectedPoints: [1] })

    const [, traces, layout] = Plotly.newPlot.mock.calls[0]
    expect(selectedOf(traces)).toEqual([[1], [1]])
    expect(layout.selections).toEqual([])
  })

  // A reload of the batch hands the chart new traces. Drawn without the
  // selection, with the selection restyled back on afterwards, the new data
  // shows unselected in between and the selected points blink.
  it('draws the selection in the same redraw as new data', async () => {
    const wrapper = await mountChart([peak(), peak()], { selectedPoints: [1] })
    vi.clearAllMocks()

    await wrapper.setProps({ data: [peak(), peak()] })
    await flushPromises()

    expect(Plotly.react).toHaveBeenCalledTimes(1)
    expect(selectedOf(lastDrawn())).toEqual([[1], [1]])
    expect(Plotly.restyle).not.toHaveBeenCalled()
    expect(Plotly.update).not.toHaveBeenCalled()
  })

  // A reload restores the selection on the new records in the same tick.
  it('draws new data and a new selection together once', async () => {
    const wrapper = await mountChart([peak(), peak()], { selectedPoints: [1] })
    vi.clearAllMocks()

    await wrapper.setProps({ data: [peak(), peak()], selectedPoints: [0] })
    await flushPromises()

    expect(Plotly.react).toHaveBeenCalledTimes(1)
    expect(selectedOf(lastDrawn())).toEqual([[0], [0]])
    expect(Plotly.update).not.toHaveBeenCalled()
  })

  // Redrawn from the traces, a selection change would put back a trace the
  // user hid from the legend: Plotly keeps that on the traces it holds.
  it('draws a new selection over the data Plotly has', async () => {
    const wrapper = await mountChart([peak()], { selectedPoints: [0] })
    vi.clearAllMocks()

    await wrapper.setProps({ selectedPoints: [0, 1] })
    await flushPromises()

    expect(Plotly.react).not.toHaveBeenCalled()
    expect(lastUpdate()).toEqual([{ selectedpoints: [[0, 1]] }, { selections: [] }])
  })

  // A box select over some traces dims the others; the selection the parent
  // takes from it has to go back on all of them, even when it is the same one.
  it('draws the same selection again when it is given anew', async () => {
    const wrapper = await mountChart([peak()], { selectedPoints: [0] })
    vi.clearAllMocks()

    await wrapper.setProps({ selectedPoints: [0] })
    await flushPromises()

    expect(lastUpdate()).toEqual([{ selectedpoints: [[0]] }, { selections: [] }])
  })

  it('draws no selection once it is cleared', async () => {
    const wrapper = await mountChart([peak()], { selectedPoints: [0] })

    await wrapper.setProps({ selectedPoints: null })
    await flushPromises()
    expect(lastUpdate()).toEqual([{ selectedpoints: [null] }, { selections: [] }])

    // An empty selection would dim every point in Plotly; it means none here.
    await wrapper.setProps({ selectedPoints: [] })
    await flushPromises()
    expect(lastUpdate()).toEqual([{ selectedpoints: [null] }, { selections: [] }])

    await wrapper.setProps({ data: [peak()] })
    await flushPromises()
    expect(lastDrawn()[0]).not.toHaveProperty('selectedpoints')
  })

  it('selects on a copy, leaving the trace it was given alone', async () => {
    const marker = peak()
    await mountChart([marker], { selectedPoints: [0] })

    expect(lastDrawn()[0].selectedpoints).toEqual([0])
    expect(marker).not.toHaveProperty('selectedpoints')
  })

  it('keeps the selection on a dotted trace', async () => {
    const wrapper = await mountChart([signal()], { selectedPoints: [3] })
    await drawn(wrapper, [0, 10], 100)

    expect(lastDrawn()[0].mode).toBe('lines+markers')
    expect(lastDrawn()[0].selectedpoints).toEqual([3])
    expect(lastDrawn()[0]).not.toHaveProperty('markSamples')
  })

  // Real Plotly keeps the traces and layout it was handed and writes a box
  // select into them: the points it took into each trace (none on a trace it
  // missed), its outline into the layout. Left there, the next redraw draws the
  // box again, whatever selection the parent has moved on to.
  describe('after a box select', () => {
    /** A box select over `points` of the first trace, as Plotly leaves it. */
    function boxSelect(points) {
      lastDrawn().forEach((trace, i) => (trace.selectedpoints = i === 0 ? [...points] : []))
      lastLayout().selections = [{ type: 'rect', x0: 0, x1: 1, y0: 0, y1: 1 }]
      handlers.plotly_selected({ points: points.map((pointIndex) => ({ pointIndex })) })
    }

    /** A redraw from the traces Plotly already has, as after a pane change. */
    async function redrawInPlace() {
      const app = useApp()
      app.ui.split.right = 0
      await flushPromises()
      app.ui.split.right = 50
      await flushPromises()
    }

    it('draws the selection the parent moves on to, not the box', async () => {
      const marker = peak()
      const wrapper = await mountChart([marker, peak()])

      boxSelect([3])
      expect(wrapper.emitted('select').at(-1)).toEqual([{ points: [3] }])
      await wrapper.setProps({ selectedPoints: [3] })
      await flushPromises()
      await wrapper.setProps({ selectedPoints: [8] })
      await flushPromises()
      expect(lastUpdate()).toEqual([{ selectedpoints: [[8]] }, { selections: [] }])

      await redrawInPlace()
      expect(selectedOf(lastDrawn())).toEqual([[8], [8]])
      expect(lastLayout().selections).toEqual([])
      expect(marker).not.toHaveProperty('selectedpoints')
    })

    it('draws no selection once the parent clears it', async () => {
      const marker = peak()
      const wrapper = await mountChart([marker, peak()])

      boxSelect([3])
      await wrapper.setProps({ selectedPoints: [3] })
      await flushPromises()
      await wrapper.setProps({ selectedPoints: null })
      await flushPromises()
      expect(lastUpdate()).toEqual([{ selectedpoints: [null] }, { selections: [] }])

      await redrawInPlace()
      expect(selectedOf(lastDrawn())).toEqual([undefined, undefined])
      expect(lastLayout().selections).toEqual([])
      expect(marker).not.toHaveProperty('selectedpoints')
    })
  })
})
