import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { toRaw } from 'vue'

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
vi.mock('@/stores', () => ({ useApp: () => ({ ui: { split: { right: 50 } } }) }))

const { default: Plotly } = await import('plotly.js-dist-min')
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

async function mountChart(data) {
  const wrapper = mount(BaseChartPlotly, {
    props: {
      id: 'chart',
      title: 'Chart',
      data,
      layout: { xaxis: { autorange: true }, yaxis: { autorange: true } }
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

  it('leaves a trace that does not ask for dots as it was given', async () => {
    const marker = peak()
    const wrapper = await mountChart([signal(), marker])

    await drawn(wrapper, [0, 10], 100)
    expect(toRaw(lastDrawn()[1])).toBe(marker)
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
