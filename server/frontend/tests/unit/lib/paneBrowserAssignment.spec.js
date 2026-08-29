import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref } from 'vue'

// The real badge, imported past the `@/lib/base` barrel mock below: what the
// ledger's verdict column renders for a row with no verdict is half of the
// formula-less-row guard, and a stub could not tell us.
import BaseVerdictBadge from '@/lib/base/BaseVerdictBadge.vue'

// The per-sample launcher's job after the assign endpoint became synchronous:
// a run that is refused (409) or a sample that cannot be assigned (422) arrives
// as a rejection, and must land as a readable reason in the pane rather than an
// uncaught promise behind a dialog that never closes.

const assign = vi.fn()
// Module-level so assertions see the same mock the component called: makeApp()
// runs afresh on every useApp() and would otherwise hand out new spies.
const sampleUnfocus = vi.fn()
const peakUnfocus = vi.fn()
// Reads `verdictRecord` at call time, so a test can set it after mounting.
// `verdictPeakId`, when a test sets one, additionally models the real store's
// identity lookup: a record is found only for the peak it was recorded against.
// Records only ever carry an M0's `sample_peak_id`, so that is what makes the
// family-resolution tests below non-vacuous - without it every row would answer
// with the same verdict whether it was resolved to its M0 or not.
const forAssignment = vi.fn((row) =>
  verdictPeakId && row?.sample_peak_id !== verdictPeakId ? null : verdictRecord
)

const SAMPLE = { sample_item_id: 'si-1', sample_item_name: 'Sample 1' }
const BATCH = { sample_batch_id: 'sb-1', sample_batch_name: 'Batch 1' }

let focusedSampleId
let runList
let runError
let assignmentList
let childrenByOwner
let byId
let verdictRecord
let verdictPeakId

// Minimal help-mode facade: the pane registers help cards through these calls;
// the tests only need them to resolve.
const helpStub = {
  set: vi.fn(),
  docUrl: (path = '') => `/docs/${path}`,
  directive: () => ({}),
  right: () => ({}),
  left: () => ({}),
  top: () => ({}),
  bottom: () => ({}),
  bottom_end: () => ({})
}

function makeApp() {
  return {
    data: {
      sample: {
        focused: focusedSampleId.value ? SAMPLE : null,
        focusedId: focusedSampleId.value,
        unfocus: sampleUnfocus
      },
      batch: { focused: BATCH },
      peak: { list: [], focused: null, focus: vi.fn(), unfocus: peakUnfocus },
      ionization: { mechanism: { list: [] } },
      peakAssignment: {
        run: {
          list: runList,
          error: runError,
          focused: runList[0] ?? null,
          focus: vi.fn(),
          unfocus: vi.fn(),
          assign
        },
        peak: {
          list: assignmentList,
          pending: false,
          tierCounts: {},
          childrenOf: (id) => childrenByOwner.get(id) ?? [],
          // Stands in for the store's family resolution; the rule itself is
          // pinned against the real implementation in
          // stores/data/modules/peakAssignment/assignment.spec.js.
          m0Of: (row) =>
            row?.role === 'iso_child' ? (byId.get(row.owner_peak_assignment_id) ?? row) : row,
          forPeak: () => null
        },
        verification: { forAssignment }
      }
    },
    ui: { tab: { active: 'sample' }, help: helpStub }
  }
}

vi.mock('@/stores', () => ({ useApp: () => makeApp() }))

vi.mock('@/lib/base', async () => ({
  BaseTabbedPanel: { template: '<div><slot name="menu" /><slot /></div>' },
  BaseCopyableField: true,
  BaseLoadError: true,
  BaseTierTag: true,
  BaseVerdictBadge: true,
  // Real: the point of the run-provenance test below is that the pane hands
  // this component each run, which a stub could not tell us.
  BaseRunProvenance: (await vi.importActual('@/lib/base/BaseRunProvenance.vue')).default
}))

vi.mock('@/lib/dialogs', () => ({ PeakAssignConfigForm: true }))

const GLOBAL_STUBS = {
  Dialog: { template: '<div><slot /><slot name="footer" /></div>' },
  Message: { template: '<div class="pane-message"><slot /></div>' },
  Button: { template: '<button><slot /></button>' },
  // Renders both slots the run selector fills, so what a user sees closed and
  // what they see in the open list are both under test.
  Select: {
    props: ['options', 'modelValue'],
    template:
      '<div class="select">' +
      '<span class="select-value"><slot name="value" :value="modelValue" placeholder="Select run" /></span>' +
      '<span v-for="o in options" :key="o.peak_assignment_run_id" class="select-option">' +
      '<slot name="option" :option="o" /></span>' +
      '</div>'
  },
  // Declared rather than auto-stubbed so the sizing tests can read what the
  // pane asked the table for and the sort tests can assert the rows are handed
  // over verbatim; the types match PrimeVue's, so bare attributes
  // (`scrollable`, `lazy`, `removableSort`) cast to true the way they do on
  // the real component instead of arriving as ''.
  DataTable: {
    name: 'DataTable',
    props: {
      value: { type: Array, default: () => [] },
      scrollable: { type: Boolean, default: false },
      scrollHeight: { type: String, default: null },
      virtualScrollerOptions: { type: Object, default: null },
      lazy: { type: Boolean, default: false },
      removableSort: { type: Boolean, default: false },
      sortField: { type: String, default: null },
      sortOrder: { type: Number, default: null }
    },
    inheritAttrs: false,
    template: '<div class="datatable"><slot /></div>'
  },
  Column: true,
  ProgressSpinner: true,
  ToggleSwitch: true
}

/** An axios-shaped rejection carrying a server message. */
function apiError(status, message) {
  return { response: { status, data: { error: message } } }
}

const { default: PaneBrowserAssignment } =
  await import('@/lib/panes/PaneBrowserMatch/PaneBrowserAssignment.vue')

// Imported statically (vi.mock is hoisted above it, so the stubs still apply):
// compiling this pane is the expensive part, and as a dynamic import inside the
// first test it lands on that one test's clock.
async function mountPane() {
  const wrapper = mount(PaneBrowserAssignment, {
    global: {
      stubs: GLOBAL_STUBS,
      directives: { tooltip: {}, help: {} }
    }
  })
  await wrapper.vm.$nextTick()
  return wrapper
}

/** An M0 assignment with its isotopologue satellites, as the ledger sees them. */
function family({
  id,
  mz,
  intensity,
  formula,
  tier = 'assigned',
  fit = 0.9,
  role = 'target',
  children = []
}) {
  const parent = {
    peak_assignment_id: id,
    sample_peak_id: `p-${id}`,
    sample_peak_mz: mz,
    sample_peak_intensity: intensity,
    assigned_formula: formula,
    tier,
    fit_score: fit,
    role
  }
  const kids = children.map((child, index) => ({
    peak_assignment_id: `${id}-c${index}`,
    sample_peak_id: `p-${id}-c${index}`,
    owner_peak_assignment_id: id,
    role: 'iso_child',
    tier,
    // The engine copies the M0's formula and mechanism onto every satellite, so
    // `sample_peak_id` is the only identity field that differs across a family.
    // Leaving them off here would let the ledger's formula guard stand in for
    // the family resolution and hide whether the resolution happens at all.
    assigned_formula: formula,
    ionization_mechanism_id: 'mech-1',
    isotope_label: `M+${index + 1}`,
    ...child
  }))
  return { parent, kids }
}

/** Put families into the mocked assignment store (parents and children flat, as
 *  the API returns them, plus the childrenOf index the pane folds them with). */
function seed(...families) {
  assignmentList = families.flatMap(({ parent, kids }) => [parent, ...kids])
  childrenByOwner = new Map(families.map(({ parent, kids }) => [parent.peak_assignment_id, kids]))
  byId = new Map(assignmentList.map((row) => [row.peak_assignment_id, row]))
}

// Two families whose satellites are nowhere near their parent on any axis: A's
// parent is the most intense peak in the sample while its children are the two
// weakest, and B's brightest child outshines both parents. A flat sort by
// intensity therefore interleaves them completely, which is exactly the bug.
const FAMILY_A = family({
  id: 'a',
  mz: 200.1,
  intensity: 1000,
  formula: 'C10H12',
  tier: 'candidate',
  fit: 0.7,
  children: [
    { sample_peak_mz: 201.1, sample_peak_intensity: 5 },
    { sample_peak_mz: 202.1, sample_peak_intensity: 6 }
  ]
})
const FAMILY_B = family({
  id: 'b',
  mz: 100.05,
  intensity: 500,
  formula: 'C2H6',
  tier: 'assigned',
  fit: 0.95,
  children: [
    { sample_peak_mz: 101.05, sample_peak_intensity: 800 },
    { sample_peak_mz: 102.05, sample_peak_intensity: 4 }
  ]
})

const ids = (wrapper) => wrapper.vm.rows.map((row) => row.peak_assignment_id)

/** Null when every family is one contiguous block led by its parent, with the
 *  satellites in m/z order; otherwise the reason it is not. */
function familyBreak(rows) {
  const seen = new Set()
  let owner = null
  let previousChildMz = -Infinity
  for (const row of rows) {
    if (row.isChild) {
      if (row.owner_peak_assignment_id !== owner) {
        return `${row.peak_assignment_id} is under ${owner ?? 'nothing'}, not its parent`
      }
      if (row.sample_peak_mz < previousChildMz) {
        return `satellites of ${owner} are not in m/z order`
      }
      previousChildMz = row.sample_peak_mz
    } else {
      if (seen.has(row.peak_assignment_id)) return `${row.peak_assignment_id} appears twice`
      seen.add(row.peak_assignment_id)
      owner = row.peak_assignment_id
      previousChildMz = -Infinity
    }
  }
  return null
}

// Reset before every test in the file, including the describes below that only
// override part of it.
beforeEach(() => {
  focusedSampleId = ref('si-1')
  runList = []
  runError = null
  verdictRecord = null
  verdictPeakId = null
  seed()
})

describe('PaneBrowserAssignment launcher', () => {
  beforeEach(() => {
    focusedSampleId = ref('si-1')
    runList = []
    assign.mockReset()
    assign.mockResolvedValue({ data: [{ peak_assignment_run_id: 'run-1' }] })
  })
  afterEach(() => vi.clearAllMocks())

  it('launches with the config and reports nothing when accepted', async () => {
    const wrapper = await mountPane()
    wrapper.vm.configVisible = true
    await wrapper.vm.$nextTick()

    await wrapper.vm.launch()

    expect(assign).toHaveBeenCalledTimes(1)
    expect(assign.mock.calls[0][0]).toBe('si-1')
    expect(assign.mock.calls[0][1].run_untargeted).toBe(true)
    expect(wrapper.vm.launchError).toBeNull()
    expect(wrapper.vm.configVisible).toBe(false)
  })

  it('closes the dialog and shows the reason when the sample is ineligible', async () => {
    assign.mockRejectedValue(
      apiError(
        422,
        "Peak assignment is not possible for sample 'Sample 1': blank sample (no peaks)."
      )
    )
    const wrapper = await mountPane()
    wrapper.vm.configVisible = true
    await wrapper.vm.$nextTick()

    await wrapper.vm.launch()
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.configVisible).toBe(false)
    expect(wrapper.vm.launchRefused).toBe(true)
    expect(wrapper.vm.launchError).toContain('blank sample')
    expect(wrapper.find('.pane-message').text()).toContain('blank sample')
  })

  it('shows the in-flight refusal when a run is already assigning the sample', async () => {
    assign.mockRejectedValue(
      apiError(409, "Peak assignment is already running for sample 'Sample 1'.")
    )
    const wrapper = await mountPane()

    await wrapper.vm.launch()

    expect(wrapper.vm.launchRefused).toBe(true)
    expect(wrapper.vm.launchError).toContain('already running')
    expect(wrapper.vm.submitting).toBe(false)
  })

  it('distinguishes a genuine failure from a refusal', async () => {
    assign.mockRejectedValue(apiError(500, 'Unexpected error (ref: abc12345).'))
    const wrapper = await mountPane()

    await wrapper.vm.launch()

    expect(wrapper.vm.launchRefused).toBe(false)
    expect(wrapper.vm.launchError).toContain('Unexpected error')
  })

  it('clears a previous refusal when the dialog is reopened', async () => {
    assign.mockRejectedValue(apiError(409, 'busy'))
    const wrapper = await mountPane()

    await wrapper.vm.launch()
    expect(wrapper.vm.launchError).toBe('busy')

    wrapper.vm.configVisible = true
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.launchError).toBeNull()
  })

  it('does nothing without a focused sample', async () => {
    focusedSampleId = ref(null)
    const wrapper = await mountPane()

    await wrapper.vm.launch()

    expect(assign).not.toHaveBeenCalled()
  })
})

// The run selector is where a reader learns which engine produced the ledger
// they are looking at. It matters here rather than only in the badge's own test
// because the pane auto-shows the newest completed run whatever produced it: an
// imported run is what a user sees by default, without ever opening the list.
describe('PaneBrowserAssignment run provenance', () => {
  beforeEach(() => {
    focusedSampleId = ref('si-1')
    assign.mockReset()
    assign.mockResolvedValue({ data: [{ peak_assignment_run_id: 'run-1' }] })
  })
  afterEach(() => vi.clearAllMocks())

  const IMPORTED = {
    peak_assignment_run_id: 'run-2',
    engine: 'peaky',
    engine_version: '0.6.0',
    status: 'completed',
    tier_bands: { assigned: 0.6, candidate: 0.3 },
    calibration: { method: 'offset-aware' }
  }
  const IN_APP = {
    peak_assignment_run_id: 'run-1',
    engine: 'mascope',
    engine_version: '0.2.0',
    status: 'completed',
    tier_bands: { assigned: 0.8, candidate: 0.5 },
    calibration: null
  }

  // The menu holds two Selects (runs, then the verdict filter) and the stub is
  // generic, so scope every query to the first one.
  const runSelect = (wrapper) => wrapper.findAll('.select')[0]

  it('names the producing engine on the selected run, not only in the open list', async () => {
    runList = [IMPORTED, IN_APP]
    const wrapper = await mountPane()

    expect(runSelect(wrapper).find('.select-value').text()).toContain('peaky')
  })

  it('keeps the run label in the closed selector', async () => {
    // The #value slot is handed the raw v-model value - the record off the run
    // store - not the matched option, so a label carried only on the option
    // copy renders blank here while every other assertion still passes.
    runList = [IMPORTED, IN_APP]
    const wrapper = await mountPane()
    const closed = runSelect(wrapper).find('.select-value').text()

    expect(closed).toContain('#2')
    expect(closed).toContain('completed')
  })

  it('carries each run its own provenance in the list', async () => {
    runList = [IMPORTED, IN_APP]
    const wrapper = await mountPane()
    const options = runSelect(wrapper).findAll('.select-option')

    expect(options).toHaveLength(2)
    // Newest first, so the import is #2 and the in-app run is #1.
    expect(options[0].text()).toContain('#2')
    expect(options[0].text()).toContain('peaky 0.6.0')
    expect(options[0].text()).toContain('calibration')
    expect(options[1].text()).toContain('Mascope 0.2.0')
    // An in-app run has no disclosure to make: its calibration is the sample's.
    expect(options[1].text()).not.toContain('calibration')
  })

  it('still marks an in-flight import as in progress', async () => {
    runList = [{ ...IMPORTED, status: 'importing' }]
    const wrapper = await mountPane()

    expect(runSelect(wrapper).find('.select-option').text()).toContain('importing…')
  })
})

// The ledger used to be sized as the window height minus a constant 60, which
// was meant to stand in for the switch bar and the tier strip and covered
// neither the launch-error banner nor a wrapped strip. It now takes whatever
// the column leaves it.
describe('PaneBrowserAssignment ledger sizing', () => {
  beforeEach(() => {
    focusedSampleId = ref('si-1')
    runList = [{ peak_assignment_run_id: 'run-1', engine: 'mascope', status: 'completed' }]
    assign.mockReset()
    assign.mockRejectedValue(apiError(409, 'already running'))
  })
  afterEach(() => vi.clearAllMocks())

  it('sizes the ledger from its pane rather than the window', async () => {
    const wrapper = await mountPane()
    const table = wrapper.findComponent({ name: 'DataTable' })

    expect(table.props('scrollHeight')).toBe('flex')
    // Both are preconditions, not decoration: PrimeVue only applies the flex
    // scroll layout when the table is scrollable, and without virtual scroller
    // options it would set an invalid `max-height: flex` instead.
    expect(table.props('scrollable')).toBe(true)
    expect(table.props('virtualScrollerOptions')).toBeTruthy()
  })

  it('keeps the ledger beside a launch error instead of behind it', async () => {
    const wrapper = await mountPane()

    await wrapper.vm.launch()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.pane-message').text()).toContain('already running')
    // The banner is a sibling row of the ledger column, so it shortens the
    // table by its own height rather than pushing it out of the pane.
    expect(wrapper.find('.ledger').exists()).toBe(true)
    expect(wrapper.findComponent({ name: 'DataTable' }).props('scrollHeight')).toBe('flex')
  })
})

// Sorting is the pane's job, not DataTable's: PrimeVue sorts the flat array it
// is handed and would scatter every isotopologue satellite away from the parent
// whose formula names it. These assert on `rows`, which with `lazy` set is
// exactly the order the table renders.
describe('PaneBrowserAssignment isotopologue grouping', () => {
  const SORTABLE_COLUMNS = [
    'sample_peak_mz',
    'sample_peak_intensity',
    'assigned_formula',
    'mech',
    'tierRank',
    'pCorrect'
  ]

  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
    seed(FAMILY_A, FAMILY_B)
  })
  afterEach(() => vi.clearAllMocks())

  async function unfolded() {
    const wrapper = await mountPane()
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    return wrapper
  }

  it('keeps each satellite under its own parent when sorting by intensity', async () => {
    const wrapper = await unfolded()

    wrapper.vm.sortField = 'sample_peak_intensity'
    wrapper.vm.sortOrder = -1
    await wrapper.vm.$nextTick()
    // Loudest parent first, each family whole. Flat sorting would have put B's
    // 800-intensity child second, above its own parent.
    expect(ids(wrapper)).toEqual(['a', 'a-c0', 'a-c1', 'b', 'b-c0', 'b-c1'])

    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['b', 'b-c0', 'b-c1', 'a', 'a-c0', 'a-c1'])
  })

  it('keeps families whole under every sortable column, both directions', async () => {
    const wrapper = await unfolded()

    for (const field of SORTABLE_COLUMNS) {
      for (const order of [1, -1]) {
        wrapper.vm.sortField = field
        wrapper.vm.sortOrder = order
        await wrapper.vm.$nextTick()

        expect(ids(wrapper)).toHaveLength(6)
        expect(familyBreak(wrapper.vm.rows), `sorting by ${field} (${order})`).toBeNull()
      }
    }
  })

  it('returns to the confidence order when the sort is removed', async () => {
    const wrapper = await unfolded()

    wrapper.vm.sortField = 'sample_peak_intensity'
    wrapper.vm.sortOrder = -1
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)[0]).toBe('a')

    // A third header click clears the field; the ledger falls back to
    // assigned-first, which puts B on top despite its weaker peak.
    wrapper.vm.sortField = null
    wrapper.vm.sortOrder = null
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['b', 'b-c0', 'b-c1', 'a', 'a-c0', 'a-c1'])
  })

  // Without this the grouping above is computed and then thrown away: PrimeVue
  // re-sorts the flat array it is given, which is what scattered the satellites
  // in the first place. `lazy` is the only thing that stops it, so it is worth
  // an assertion of its own - every ordering test above would pass without it.
  it('hands the table its rows to render, not to re-sort', async () => {
    const wrapper = await mountPane()
    const table = wrapper.findComponent({ name: 'DataTable' })

    expect(table.props('lazy')).toBe(true)
    expect(table.props('value')).toStrictEqual(wrapper.vm.rows)
    // A third click on a sorted header clears the column rather than cycling.
    expect(table.props('removableSort')).toBe(true)
  })

  it('leaves the folded default alone', async () => {
    const wrapper = await mountPane()

    expect(ids(wrapper)).toEqual(['b', 'a'])
  })

  it('sorts unassigned peaks last rather than treating a missing formula as smallest', async () => {
    seed(FAMILY_A, FAMILY_B, family({ id: 'c', mz: 50.1, intensity: 90, formula: null }))
    const wrapper = await mountPane()

    wrapper.vm.sortField = 'assigned_formula'
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()
    // C2H6 before C10H12 (see the collation test below), and the peak with no
    // formula last in BOTH directions.
    expect(ids(wrapper)).toEqual(['b', 'a', 'c'])

    wrapper.vm.sortOrder = -1
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['a', 'b', 'c'])
  })

  it('treats an empty formula as missing, not as the smallest string', async () => {
    seed(FAMILY_B, family({ id: 'e', mz: 60.1, intensity: 70, formula: '' }))
    const wrapper = await mountPane()

    wrapper.vm.sortField = 'assigned_formula'
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['b', 'e'])
  })

  // The ledger took the sort over from PrimeVue, whose comparer collated
  // numerically. Losing that silently shreds a homologous series - the one
  // ordering a formula column exists to show - so it is pinned here.
  it('orders formulas by carbon count, not by digit position', async () => {
    seed(
      family({ id: 'c12', mz: 170.2, intensity: 10, formula: 'C12H26' }),
      family({ id: 'c2', mz: 30.1, intensity: 20, formula: 'C2H6' }),
      family({ id: 'c10', mz: 142.2, intensity: 30, formula: 'C10H22' }),
      family({ id: 'c3', mz: 44.1, intensity: 40, formula: 'C3H8' }),
      family({ id: 'c9', mz: 128.2, intensity: 50, formula: 'C9H20' })
    )
    const wrapper = await mountPane()

    wrapper.vm.sortField = 'assigned_formula'
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()

    expect(ids(wrapper)).toEqual(['c2', 'c3', 'c9', 'c10', 'c12'])
  })
})

// The engine writes adduct corroboration onto the M0 winner alone: a satellite
// is the same ion measured at another isotope, not a second sighting of the
// compound, so the backend leaves its count null by construction. The marker
// still belongs on the satellite rows - the evidence is about the formula the
// whole family shares - it just has to say the count is the family's.
describe('PaneBrowserAssignment adduct corroboration', () => {
  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
  })
  afterEach(() => vi.clearAllMocks())

  /** A family whose M0 was corroborated by `n` adducts (null for none). Its
   *  satellites carry whatever the backend put on them, which is nothing. */
  function corroborated(n, children = [{}, {}]) {
    const fam = family({
      id: 'a',
      mz: 200.1,
      intensity: 1000,
      formula: 'C10H12',
      children: children.map((child, index) => ({
        sample_peak_mz: 201.1 + index,
        sample_peak_intensity: 50 - index,
        corroboration_adducts: null,
        ...child
      }))
    })
    fam.parent.corroboration_adducts = n
    return fam
  }

  async function unfolded(...families) {
    seed(...families)
    const wrapper = await mountPane()
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    return wrapper
  }

  const rowsById = (wrapper) => new Map(wrapper.vm.rows.map((row) => [row.peak_assignment_id, row]))

  it('carries the family count onto satellite rows, marked inherited', async () => {
    const wrapper = await unfolded(corroborated(3))
    const rows = rowsById(wrapper)

    // Every satellite shows the count, and says it is not its own.
    for (const id of ['a-c0', 'a-c1']) {
      expect(rows.get(id).corrobAdducts, id).toBe(3)
      expect(rows.get(id).corrobInherited, id).toBe(true)
    }
    // The M0's own count is unchanged and not flagged as borrowed.
    expect(rows.get('a').corrobAdducts).toBe(3)
    expect(rows.get('a').corrobInherited).toBe(false)
  })

  // The engine folds the boost into the record that carries the corroboration -
  // the M0's p_correct - and never into a satellite's. So the satellite's
  // tooltip must not claim the number it sits beside already accounts for it,
  // which is exactly what the M0's own wording says.
  it('says whose evidence it is, and whose P(correct) has the boost', async () => {
    const wrapper = await unfolded(corroborated(3))
    const rows = rowsById(wrapper)

    expect(wrapper.vm.corrobTooltip(rows.get('a-c0'))).toBe(
      'Supported by 3 adducts, via the M0 of this isotopologue family ' +
        "(folded into the M0's P(correct), not into this row's)"
    )
    // The M0's own tooltip is the one it always had.
    expect(wrapper.vm.corrobTooltip(rows.get('a'))).toBe(
      'Supported by 3 adducts (already folded into P(correct))'
    )
  })

  // The count itself is the same number the M0 shows, so the marker parenthesises
  // a borrowed one rather than dimming it - dimming would borrow the "no value
  // here" idiom the uncalibrated P(correct) state already owns in this column.
  it('parenthesises a borrowed count and leaves an owned one bare', async () => {
    const wrapper = await unfolded(corroborated(3))
    const rows = rowsById(wrapper)

    expect(wrapper.vm.corrobLabel(rows.get('a-c0'))).toBe('(3)')
    expect(wrapper.vm.corrobLabel(rows.get('a'))).toBe('3')
  })

  // The marker is gated on `corrobAdducts > 1`, so an uncorroborated family has
  // to stay at 0 rather than inherit a 1 or a null that reads as "supported".
  it('leaves a family whose M0 was not corroborated unmarked', async () => {
    const wrapper = await unfolded(corroborated(null))
    const rows = rowsById(wrapper)

    for (const id of ['a', 'a-c0', 'a-c1']) {
      expect(rows.get(id).corrobAdducts, id).toBe(0)
      // Nothing was borrowed: there was no count to borrow.
      expect(rows.get(id).corrobInherited, id).toBe(false)
    }
  })

  // A lone adduct corroborates nothing, so the marker's `> 1` gate hides it. The
  // count still travels to the satellites, and the row says so - `corrobInherited`
  // tracks where the number came from, not whether it happens to render.
  it('keeps a single-adduct count below the marker threshold', async () => {
    const wrapper = await unfolded(corroborated(1))
    const rows = rowsById(wrapper)

    for (const id of ['a-c0', 'a-c1']) {
      expect(rows.get(id).corrobAdducts, id).toBe(1)
      expect(rows.get(id).corrobInherited, id).toBe(true)
    }
    expect(rows.get('a').corrobInherited).toBe(false)
  })

  // An imported ledger is not bound by the in-app engine's winner-only rule, so
  // a satellite that does carry its own count keeps it rather than the family's.
  it('prefers a satellite own count over the family one', async () => {
    const wrapper = await unfolded(corroborated(3, [{ corroboration_adducts: 2 }, {}]))
    const rows = rowsById(wrapper)

    expect(rows.get('a-c0').corrobAdducts).toBe(2)
    expect(rows.get('a-c0').corrobInherited).toBe(false)
    expect(rows.get('a-c1').corrobAdducts).toBe(3)
    expect(rows.get('a-c1').corrobInherited).toBe(true)
  })

  // Each satellite inherits from ITS OWN parent, not from whichever family the
  // loop happened to reach first. With one family on screen the two are
  // indistinguishable, so this is the case that pins the per-parent scope.
  it('inherits from each satellite own parent, not across families', async () => {
    const corroboratedFamily = corroborated(3)
    const bare = family({
      id: 'b',
      mz: 100.05,
      intensity: 500,
      formula: 'C2H6',
      children: [{ sample_peak_mz: 101.05, sample_peak_intensity: 40 }]
    })
    bare.parent.corroboration_adducts = null
    const wrapper = await unfolded(corroboratedFamily, bare)
    const rows = rowsById(wrapper)

    expect(rows.get('a-c0').corrobAdducts).toBe(3)
    expect(rows.get('a-c0').corrobInherited).toBe(true)
    // The uncorroborated family's satellite has no claim on the other's count.
    expect(rows.get('b-c0').corrobAdducts).toBe(0)
    expect(rows.get('b-c0').corrobInherited).toBe(false)
  })

  // Everything above asserts on `rows`, which the shared stubs never render:
  // PrimeVue's DataTable is what feeds each row to a Column's #body slot, and
  // `DataTable` is a bare div here while `Column` is auto-stubbed. So the markup
  // this change actually ships - the marker's gate and its parenthesised label -
  // is invisible to those tests, and renaming the binding would leave them all
  // green. This pair passes the rows down so the rendered cell can be read.
  async function rendered(...families) {
    // A ref, not a captured array: the rows change when the family is unfolded
    // below, and the Column stub has to re-render with them.
    const tableRows = ref([])
    seed(...families)
    const wrapper = mount(PaneBrowserAssignment, {
      global: {
        directives: { tooltip: {}, help: {} },
        stubs: {
          ...GLOBAL_STUBS,
          // Keeps the shared stub's prop declarations, so `scrollHeight` and
          // friends stay props rather than falling through onto the div.
          DataTable: {
            ...GLOBAL_STUBS.DataTable,
            watch: {
              value: { handler: (value) => (tableRows.value = value), immediate: true }
            }
          },
          Column: {
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="col"><template v-for="(row, i) in rows" :key="i">' +
              '<slot name="body" :data="row" /></template></div>'
          }
        }
      }
    })
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    return wrapper
  }

  it('renders a parenthesised marker on satellites and a bare one on the M0', async () => {
    const wrapper = await rendered(corroborated(3))
    const marks = wrapper.findAll('.corrob-mark').map((m) => m.text())

    // One per row of the family, M0 first: its own count bare, the two borrowed
    // ones parenthesised.
    expect(marks).toEqual(['3', '(3)', '(3)'])
  })

  it('renders no marker for a family whose M0 was not corroborated', async () => {
    const wrapper = await rendered(corroborated(null))

    expect(wrapper.findAll('.corrob-mark')).toHaveLength(0)
  })

  // Folded is the default view, and the parent row is the only one on screen.
  it('leaves the folded M0 row alone', async () => {
    seed(corroborated(3))
    const wrapper = await mountPane()

    expect(wrapper.vm.rows).toHaveLength(1)
    expect(wrapper.vm.rows[0].corrobAdducts).toBe(3)
    expect(wrapper.vm.rows[0].corrobInherited).toBe(false)
  })
})

describe('PaneBrowserAssignment header and controls', () => {
  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
    seed(FAMILY_A, FAMILY_B)
  })
  afterEach(() => vi.clearAllMocks())

  it('names the batch and the sample, and counts the peaks', async () => {
    const wrapper = await mountPane()

    expect(wrapper.vm.breadcrumb.items.map((item) => item.label)).toEqual([
      undefined,
      'Batch 1',
      'Sample 1',
      '6 peaks'
    ])
  })

  it('offers a way back out of the sample', async () => {
    const wrapper = await mountPane()

    wrapper.vm.breadcrumb.items[0].action()

    expect(sampleUnfocus).toHaveBeenCalledTimes(1)
  })

  it('falls back to the plain label with no sample in view', async () => {
    focusedSampleId = ref(null)
    const wrapper = await mountPane()

    expect(wrapper.vm.breadcrumb).toBeNull()
  })

  it('clears the peak focus when the selected row is clicked again', async () => {
    const wrapper = await mountPane()

    // PrimeVue emits null through v-model:selection on de-selection.
    wrapper.vm.selectedRow = null

    expect(peakUnfocus).toHaveBeenCalledTimes(1)
  })

  // A formula-less row is a placeholder for a peak nothing explained: there is
  // no assignment to have judged. A verdict left on one by an earlier run must
  // neither show a badge nor answer the verdict filter, which would sort the
  // row under a verdict its own column does not show. `verdictFor` feeds both,
  // so it is the one place the guard has to hold - and a null record is what
  // renders no badge (asserted against the real component below).
  const UNASSIGNED_ROW = {
    id: 'u',
    mz: 50.1,
    intensity: 90,
    formula: null,
    tier: 'unassigned',
    role: 'unassigned',
    fit: null
  }

  it('carries no verdict on a row with no formula', async () => {
    verdictRecord = { verdict: 'confirmed', evidence_level: 'msms' }
    seed(FAMILY_B, family(UNASSIGNED_ROW))
    const wrapper = await mountPane()

    const rowsById = new Map(wrapper.vm.rows.map((row) => [row.peak_assignment_id, row]))
    expect(wrapper.vm.verdictFor(rowsById.get('u'))).toBeNull()
    // The positive arm looks the verdict up by the whole row: the store keys on
    // `sample_peak_id|assigned_formula|ionization_mechanism_id`, so handing it
    // anything narrower would miss every time against the real one.
    expect(wrapper.vm.verdictFor(rowsById.get('b'))).toEqual(verdictRecord)
    expect(forAssignment).toHaveBeenLastCalledWith(rowsById.get('b'))
  })

  it('renders no badge for the verdict a formula-less row does not have', async () => {
    // The column body is `<BaseVerdictBadge :record="verdictFor(data)" compact />`,
    // and the stubbed table never renders it - so this pins the other half of
    // that chain on the real component: a null record is a blank cell.
    const badge = mount(BaseVerdictBadge, { props: { record: null, compact: true } })

    expect(badge.text()).toBe('')
    expect(badge.find('span').exists()).toBe(false)
  })

  it('leaves a formula-less row out of a verdict filter it cannot answer', async () => {
    verdictRecord = { verdict: 'confirmed', evidence_level: 'msms' }
    seed(FAMILY_B, family(UNASSIGNED_ROW))
    const wrapper = await mountPane()

    wrapper.vm.verdictFilter = 'confirmed'
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['b'])

    wrapper.vm.verdictFilter = 'unverified'
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['u'])
  })

  // One verdict covers the isotopologue family. An unfolded satellite used to
  // show a blank verdict cell because its own identity was looked up and the
  // record had been stored against its M0's peak - so under the `confirmed`
  // filter a family arrived on screen with a confirmed badge on the parent and
  // blanks on every child beneath it, contradicting the filter that admitted it.
  describe('family-scoped verdicts', () => {
    const VERDICT = { verdict: 'confirmed', evidence_level: 'msms' }

    /** Unfold FAMILY_B with its verdict recorded against the M0's peak. */
    async function unfoldedWithVerdict() {
      verdictRecord = VERDICT
      verdictPeakId = 'p-b'
      seed(FAMILY_B)
      const wrapper = await mountPane()
      wrapper.vm.showIsotopologues = true
      await wrapper.vm.$nextTick()
      return wrapper
    }

    const rowsById = (wrapper) => new Map(wrapper.vm.rows.map((r) => [r.peak_assignment_id, r]))

    it('shows the compound its verdict on every satellite', async () => {
      const wrapper = await unfoldedWithVerdict()
      const rows = rowsById(wrapper)

      expect(wrapper.vm.verdictFor(rows.get('b'))).toEqual(VERDICT)
      expect(wrapper.vm.verdictFor(rows.get('b-c0'))).toEqual(VERDICT)
      expect(wrapper.vm.verdictFor(rows.get('b-c1'))).toEqual(VERDICT)
    })

    it('looks the satellite up by its M0 rather than by itself', async () => {
      const wrapper = await unfoldedWithVerdict()
      const rows = rowsById(wrapper)

      wrapper.vm.verdictFor(rows.get('b-c0'))

      // The row handed to the store is the M0 off the assignment list, which is
      // where the record was written; the satellite's own identity is not asked
      // about at all.
      expect(forAssignment).toHaveBeenLastCalledWith(byId.get('b'))
    })

    // The next two are regression guards, not proof of the fix - both pass
    // against the pre-fix pane too. The filter runs over parents only and
    // re-attaches each family under whichever parent survived, so a family has
    // always been one unit here. They are worth keeping because the badge column
    // and the filter now have to agree: a filter that started judging satellites
    // individually would put a family on screen with rows missing from it.
    it('keeps a family one unit in the verdict filter', async () => {
      const wrapper = await unfoldedWithVerdict()

      expect(ids(wrapper)).toEqual(['b', 'b-c0', 'b-c1'])

      wrapper.vm.verdictFilter = 'confirmed'
      await wrapper.vm.$nextTick()
      expect(ids(wrapper)).toEqual(['b', 'b-c0', 'b-c1'])

      // Its M0 is confirmed, so no member of the family is unverified.
      wrapper.vm.verdictFilter = 'unverified'
      await wrapper.vm.$nextTick()
      expect(ids(wrapper)).toEqual([])
    })

    it('leaves an unverified family unverified all the way down', async () => {
      verdictRecord = VERDICT
      verdictPeakId = 'p-nothing'
      seed(FAMILY_B)
      const wrapper = await mountPane()
      wrapper.vm.showIsotopologues = true
      await wrapper.vm.$nextTick()

      expect(wrapper.vm.verdictFor(rowsById(wrapper).get('b-c0'))).toBeNull()

      wrapper.vm.verdictFilter = 'unverified'
      await wrapper.vm.$nextTick()
      expect(ids(wrapper)).toEqual(['b', 'b-c0', 'b-c1'])
    })

    // No ownerless-satellite case here: `childrenByOwner` skips a null owner, so
    // such a row is neither a parent row nor attached under one and never
    // reaches this table at all. It is reachable in the inspector, and covered
    // there (panePeakAssign.spec.js).
  })

  it('shows exactly one "Assign peaks" control in every state', async () => {
    const withRuns = await mountPane()
    expect(withRuns.findAll('[label="Assign peaks"]')).toHaveLength(1)

    // No runs: the empty state carries the only call to action.
    runList = []
    const withoutRuns = await mountPane()
    expect(withoutRuns.findAll('[label="Assign peaks"]')).toHaveLength(1)

    // The run list failed to load. There is no empty state to carry it here, so
    // the toolbar has to - a failed load must not also remove the way to start
    // a run.
    runList = []
    runError = new Error('nope')
    const withError = await mountPane()
    expect(withError.findAll('[label="Assign peaks"]')).toHaveLength(1)
  })
})
