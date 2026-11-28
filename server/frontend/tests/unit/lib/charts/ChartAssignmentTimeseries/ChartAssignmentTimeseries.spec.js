import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { reactive } from 'vue'

// The time series draws one line per isotopologue of the focused assignment's
// family, each named by its label and m/z. The label counts from the family's
// M0, which for a labelled ion is a bracketed line itself: for the 15N-nitrate
// ion C9H16O7^N- the M0 is [15N]C9H16O7-, and the one line without a bracket,
// C9H16NO7-, is the reagent's unlabelled remainder. Read off the brackets alone,
// the remainder was named "M0" and the ion's own line "[15N]".

let familyRows
let peaks
// The run in view, and the measurements the inspector card loaded, by the M0's
// peak_assignment_id. Reactive, so a test can hand one over after the traces.
const store = reactive({ run: null, evidence: {} })

// Plotly's own resize, which the chart's exposed one passes on to.
const { plotResize } = vi.hoisted(() => ({ plotResize: vi.fn() }))

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: {
      sample: { focusedId: 'si-1' },
      peak: { pending: false, list: peaks, focused: peaks[0] },
      peakAssignment: {
        peak: {
          get run() {
            return store.run
          },
          evidenceOf: (id) => store.evidence[id] ?? null,
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
const { api } = await import('@/api')

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

function mountChart() {
  return mount(ChartAssignmentTimeseries, {
    global: { stubs: { ToggleSwitch: true }, directives: { help: {} } }
  })
}

const namesOf = (wrapper) => wrapper.findAll('.trace').map((trace) => trace.text())

async function traceNames() {
  const wrapper = mountChart()
  await flushPromises()
  return namesOf(wrapper)
}

beforeEach(() => {
  familyRows = []
  peaks = []
  store.run = null
  store.evidence = {}
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

// A sample the batch ledger serves: its rows carry no isotope label or formula,
// so the lines are named from the measurement the inspector card took of the
// family on this sample, as the card's Isotope pattern names them.
describe('ChartAssignmentTimeseries on a ledger-served sample', () => {
  /** A ledger row: no isotope label, no isotope formula. */
  function ledgerRow(id, mz, role = 'iso_child') {
    return {
      peak_assignment_id: id,
      sample_peak_id: `p-${id}`,
      sample_peak_mz: mz,
      role,
      owner_peak_assignment_id: role === 'iso_child' ? 'm0' : null,
      ion_formula: 'C10H17N2O^+',
      isotope_formula: null,
      isotope_label: null
    }
  }

  /** The measurement of the family's composition against this sample's peaks. */
  const measurement = {
    peak_assignment_id: 'm0',
    isotopologues: [
      { sample_peak_id: 'p-m1', isotope_label: 'M+1', isotope_formula: '[13C]C9H17N2O+' },
      { sample_peak_id: 'p-m2', isotope_label: 'M+2', isotope_formula: null },
      { sample_peak_id: null, isotope_label: 'M+3', isotope_formula: '[18O]C10H17N2+' }
    ]
  }

  function seedLedger() {
    familyRows = [
      ledgerRow('m0', 185.1172, 'M0'),
      ledgerRow('m1', 186.1206),
      ledgerRow('m2', 187.1214),
      ledgerRow('m4', 189.1)
    ]
    peaks = familyRows.map((r) => ({ peak_id: r.sample_peak_id, mz: r.sample_peak_mz }))
    store.run = { engine: 'batch' }
  }

  beforeEach(() => api.http.post.mockClear())

  it('names the lines from the measurement the store holds', async () => {
    seedLedger()
    store.evidence = { m0: measurement }

    // The M0, a substitution label, an offset label without a formula, and a
    // row the measurement paired to no line, which keeps its m/z.
    expect(await traceNames()).toEqual([
      'M0 185.1172',
      '[13C] 186.1206',
      'M+2 187.1214',
      '189.1000',
      'Sum'
    ])
  })

  it('keeps the m/z without a measurement', async () => {
    seedLedger()

    expect(await traceNames()).toEqual(['185.1172', '186.1206', '187.1214', '189.1000', 'Sum'])
  })

  it('relabels when the measurement arrives, without fetching the traces again', async () => {
    seedLedger()
    const wrapper = mountChart()
    await flushPromises()
    expect(api.http.post).toHaveBeenCalledTimes(4)
    expect(namesOf(wrapper)[1]).toBe('186.1206')

    store.evidence = { m0: measurement }
    await flushPromises()

    expect(namesOf(wrapper)).toEqual([
      'M0 185.1172',
      '[13C] 186.1206',
      'M+2 187.1214',
      '189.1000',
      'Sum'
    ])
    expect(api.http.post).toHaveBeenCalledTimes(4)
  })

  // The card loads a measurement only for a ledger-served sample; one left in
  // the store from such a view does not rename a run's own lines.
  it("reads no measurement on a run's own rows", async () => {
    seedLedger()
    store.run = { engine: 'run' }
    store.evidence = { m0: measurement }

    expect(await traceNames()).toEqual(['185.1172', '186.1206', '187.1214', '189.1000', 'Sum'])
  })

  it("lets a row's own label win over the measurement", async () => {
    seedLedger()
    familyRows[1].isotope_formula = '[15N]C10H17NO+'
    familyRows[2].isotope_label = 'M+2*'
    store.evidence = { m0: measurement }

    expect(await traceNames()).toEqual([
      'M0 185.1172',
      '[15N] 186.1206',
      'M+2* 187.1214',
      '189.1000',
      'Sum'
    ])
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
