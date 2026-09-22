import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// The time series draws one line per isotopologue of the focused assignment's
// family, each named by its label and m/z. The label counts from the family's
// M0, which for a labelled ion is a bracketed line itself: for the 15N-nitrate
// ion C9H16O7^N- the M0 is [15N]C9H16O7-, and the one line without a bracket,
// C9H16NO7-, is the reagent's unlabelled remainder. Read off the brackets alone,
// the remainder was named "M0" and the ion's own line "[15N]".

let familyRows
let peaks

// Plotly's own resize, which the chart's exposed one passes on to.
const { plotResize } = vi.hoisted(() => ({ plotResize: vi.fn() }))

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: {
      sample: { focusedId: 'si-1' },
      peak: { pending: false, list: peaks, focused: peaks[0] },
      peakAssignment: {
        peak: {
          forPeak: () => familyRows[0],
          familyOf: () => familyRows,
          // The store's family resolution, over the rows this test holds.
          m0Of: (row) =>
            row?.role === 'iso_child'
              ? (familyRows.find((r) => r.peak_assignment_id === row.owner_peak_assignment_id) ??
                row)
              : row
        }
      }
    },
    ui: { tab: { active: 'sample' }, help: { docUrl: (path = '') => `/docs/${path}` } }
  })
}))

// Every peak's trace comes back on one shared time axis, so the chart adds its
// family sum as well.
vi.mock('@/api', () => ({
  api: { http: { post: vi.fn(() => Promise.resolve({ time: [0, 1], height: [10, 20] })) } }
}))

// Plotly's figure, reduced to the names of the traces it is handed.
vi.mock('@/lib/charts/BaseChartPlotly.vue', () => ({
  default: {
    props: ['id', 'title', 'data', 'layout', 'loading'],
    methods: { resize: plotResize },
    template:
      '<div><span v-for="trace in data" :key="trace.name" class="trace">{{ trace.name }}</span></div>'
  }
}))

const { default: ChartAssignmentTimeseries } =
  await import('@/lib/charts/ChartAssignmentTimeseries/ChartAssignmentTimeseries.vue')
const { default: Plot } = await import('@/lib/charts/BaseChartPlotly.vue')

/** One family row, as the ledger serves it. */
function row(id, mz, isotopeFormula, { role = 'iso_child', ionFormula = 'C9H16O7^N-' } = {}) {
  return {
    peak_assignment_id: id,
    sample_peak_id: `p-${id}`,
    sample_peak_mz: mz,
    role,
    owner_peak_assignment_id: role === 'iso_child' ? 'm0' : null,
    ion_formula: ionFormula,
    isotope_formula: isotopeFormula
  }
}

async function traceNames() {
  const wrapper = mount(ChartAssignmentTimeseries, {
    global: { stubs: { ToggleSwitch: true }, directives: { help: {} } }
  })
  await flushPromises()
  return wrapper.findAll('.trace').map((trace) => trace.text())
}

beforeEach(() => {
  familyRows = []
  peaks = []
})

describe('ChartAssignmentTimeseries trace names', () => {
  function seed(rows) {
    familyRows = rows
    peaks = rows.map((r) => ({ peak_id: r.sample_peak_id, mz: r.sample_peak_mz }))
  }

  it('counts a 15N-labelled family from its labelled line, the remainder at 14N', async () => {
    seed([
      row('m0', 251.0903, '[15N]C9H16O7-', { role: 'M0' }),
      row('rem', 250.0932, 'C9H16NO7-'),
      row('13c', 252.0936, '[13C][15N]C8H16O7-')
    ])

    expect(await traceNames()).toEqual(['M0 251.0903', '[14N] 250.0932', '[13C] 252.0936', 'Sum'])
  })

  // An imported run need not repeat the ion formula on every isotopologue.
  it("reads an isotopologue that names no ion through its M0's", async () => {
    seed([
      row('m0', 251.0903, '[15N]C9H16O7-', { role: 'M0' }),
      row('rem', 250.0932, 'C9H16NO7-', { ionFormula: null })
    ])

    expect(await traceNames()).toEqual(['M0 251.0903', '[14N] 250.0932', 'Sum'])
  })

  it('keeps the brackets of an unlabelled family', async () => {
    seed([
      row('m0', 328.6817, 'CHBr4-', { role: 'M0', ionFormula: 'CHBr4-' }),
      row('81br2', 332.6776, '[81Br]2CHBr2-', { ionFormula: 'CHBr4-' })
    ])

    expect(await traceNames()).toEqual(['M0 328.6817', '[81Br]2 332.6776', 'Sum'])
  })
})

// A splitter divider beside the chart changes its width and not its height,
// which the chart's height watcher never sees: the Sample tab asks it to resize.
describe('ChartAssignmentTimeseries resize', () => {
  it('passes a resize on to its plot', async () => {
    familyRows = [row('m0', 251.0903, '[15N]C9H16O7-', { role: 'M0' })]
    peaks = [{ peak_id: 'p-m0', mz: 251.0903 }]
    const wrapper = mount(ChartAssignmentTimeseries, {
      global: { stubs: { ToggleSwitch: true }, directives: { help: {} } }
    })
    await flushPromises()
    plotResize.mockClear()

    wrapper.vm.resize()

    expect(plotResize).toHaveBeenCalledTimes(1)
  })
})

// Outside the plot the legend takes its width off it, and the time series no
// longer lines up with the spectrum above it, which has the same margins and no
// legend.
describe('ChartAssignmentTimeseries legend', () => {
  it('sits inside the plot, at its top right', async () => {
    familyRows = [row('m0', 251.0903, '[15N]C9H16O7-', { role: 'M0' })]
    peaks = [{ peak_id: 'p-m0', mz: 251.0903 }]
    const wrapper = mount(ChartAssignmentTimeseries, {
      global: { stubs: { ToggleSwitch: true }, directives: { help: {} } }
    })
    await flushPromises()

    expect(wrapper.findComponent(Plot).props('layout').legend).toEqual({
      x: 1,
      xanchor: 'right',
      y: 1,
      yanchor: 'top'
    })
  })
})
