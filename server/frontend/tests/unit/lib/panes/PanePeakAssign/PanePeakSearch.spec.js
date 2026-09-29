import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { h, ref, Fragment } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// The write path out of the composition search: the hand button on a result row
// commits that composition onto the focused peak's ledger row. It had no
// coverage at all, and the dangerous part is not the request body (pinned in
// searchHit.spec.js) but WHICH peak the request lands on - the results table
// outlives the peak it was searched for, because it is only replaced when the
// debounced search callback runs, several hundred milliseconds after the focus
// has already moved.

const PEAK_A = { peak_id: 'p-a', mz: 200.1234, height: 12345 }
const PEAK_B = { peak_id: 'p-b', mz: 431.7001, height: 5000 }

/** A search result row, shaped as the cheminfo match endpoint builds one. */
function hit(formula, mechanism = 'mech-1') {
  return {
    target_compound_formula: formula,
    target_ion_formula: `${formula}+`,
    ionization_mechanism_id: mechanism,
    fit_score: 0.82,
    children: [{ mz: 200.1234, relative_abundance: 1.0, target_isotope_formula: formula }],
    cheminfo: {
      sample_peak_mz: PEAK_A.mz,
      target_isotope_mz: 200.1234,
      target_isotope_mz_error_ppm: -1.1,
      ionization_mechanism: { ionization_mechanism_id: mechanism }
    }
  }
}

/** The ledger row every detected peak of a run has, formula or not. */
function assignmentRow(peak, id) {
  return {
    peak_assignment_id: id,
    sample_peak_id: String(peak.peak_id),
    sample_peak_mz: peak.mz,
    assigned_formula: null,
    tier: 'unassigned',
    role: 'unassigned'
  }
}

// Module-level so assertions see the same spy the component called: makeApp()
// runs afresh on every useApp().
const curate = vi.fn(() => Promise.resolve(null))
// The socket handler the pane registers at setup; the tests deliver payloads
// through it rather than writing `results` directly, because recording which
// peak the rows belong to is part of accepting a payload.
let socketHandlers

let focusedPeak
let ledger // String(peak_id) -> assignment row, or null for "no run covers it"

const helpStub = {
  docUrl: (path = '') => `/docs/${path}`,
  top: () => ({}),
  bottom: () => ({}),
  bottom_end: () => ({}),
  left: () => ({}),
  right: () => ({})
}

function makeApp() {
  return {
    data: {
      sample: { focusedId: 'si-1', focused: null },
      // A getter over a ref so the pane's computeds re-evaluate when a test
      // moves the focus after mounting.
      peak: {
        list: [PEAK_A, PEAK_B],
        get focused() {
          return focusedPeak.value
        }
      },
      target: { compound: { list: [] } },
      ionization: { mode: { list: [] }, mechanism: { list: [] } },
      match: { params: { typeDefaults: {} } },
      peakAssignment: {
        peak: {
          forPeak: (peakId) => (peakId == null ? null : (ledger.get(String(peakId)) ?? null)),
          curate
        }
      }
    },
    ui: {
      help: helpStub,
      notification: { on: (type, handler) => socketHandlers.set(type, handler) }
    }
  }
}

vi.mock('@/stores', () => ({ useApp: () => makeApp() }))

// `/params` answers without a `peak_assignment` block, which leaves the shared
// parameter store unloaded: the pane then never launches a search of its own,
// so the results under test are exactly the ones the test delivered.
vi.mock('@/api', () => ({
  api: {
    http: {
      get: () => Promise.resolve({ data: { data: { params: {} } } }),
      post: () => Promise.resolve({})
    }
  }
}))

vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: true }))

vi.mock('@/lib/base', () => ({
  BaseTierTag: { props: ['tier', 'evidence', 'source'], template: '<span class="tier-tag" />' },
  BaseMatchTag: { props: ['matchScore'], template: '<span class="match-tag" />' }
}))

vi.mock('@/lib/dialogs', () => ({
  PopoverTargetCompoundAdd: { props: ['formula'], template: '<span class="target-add" />' }
}))

// One preview for the pane and the tests alike: a click on an isotope line puts
// that line in it for the spectrum chart.
vi.mock('@/lib/panes/PanePeakAssign/preview.js', async () => {
  const { reactive } = await import('vue')
  const preview = reactive({ peak: null })
  return { usePreview: () => preview }
})

// PrimeVue's DataTable renders nothing under a plain auto-stub, and its real
// virtual scroller renders no rows in a zero-height jsdom viewport - either way
// the row actions under test would never exist. This pair renders each Column's
// `#body` slot once per row, plus its `#header` slot once: the header is where
// the curation help card is anchored, and its lifetime is the thing under test.
// A click on a row is the table's `row-click`, as PrimeVue emits it.
const Column = {
  name: 'Column',
  props: ['field', 'header', 'sortable', 'expander'],
  render: () => null
}

const DataTable = {
  name: 'DataTable',
  props: ['value'],
  emits: ['row-click'],
  setup(props, { slots, emit }) {
    const columns = () => {
      const flat = []
      const walk = (nodes) => {
        for (const node of nodes ?? []) {
          if (node.type === Fragment) walk(node.children)
          else if (node.type?.name === 'Column') flat.push(node)
        }
      }
      walk(slots.default?.() ?? [])
      return flat
    }
    return () =>
      h('div', { class: 'dt' }, [
        h(
          'div',
          { class: 'dt-head' },
          columns().map((column) => (column.children?.header ? column.children.header({}) : null))
        ),
        ...(props.value ?? []).map((row, index) =>
          h(
            'div',
            { class: 'dt-row', key: index, onClick: () => emit('row-click', { data: row }) },
            columns().map((column) =>
              column.children?.body ? column.children.body({ data: row }) : null
            )
          )
        )
      ])
  }
}

const GLOBAL_STUBS = {
  DataTable,
  Column,
  Button: {
    props: ['disabled', 'loading', 'icon'],
    template: '<button :disabled="disabled"><slot /></button>'
  },
  FloatLabel: { template: '<div><slot /></div>' },
  InputText: true,
  InputNumber: true,
  MultiSelect: true,
  ProgressSpinner: true
}

// The help store registers a card per element and never unregisters one (see
// stores/ui/help.js), so an element carrying `v-help` that unmounts leaves a
// card behind for the rest of the session. The real store is not mounted here;
// what the pane can be held to is the lifecycle it hands the directive, so the
// stub records it.
const helpMounted = []
const helpUnmounted = []
const helpDirective = {
  mounted: (element, { value }) => helpMounted.push(value),
  unmounted: (element, { value }) => helpUnmounted.push(value)
}
const curationCards = (cards) => cards.filter((card) => card?.helpKey === 'assignment-curation')

const { default: PanePeakSearch } = await import('@/lib/panes/PanePeakAssign/PanePeakSearch.vue')

async function mountPane() {
  const wrapper = mount(PanePeakSearch, {
    props: { height: 400 },
    global: { stubs: GLOBAL_STUBS, directives: { tooltip: {}, help: helpDirective } }
  })
  await wrapper.vm.$nextTick()
  return wrapper
}

/** Deliver a result set for `peak`, the way the search socket does. */
async function deliverResults(wrapper, peak, hits) {
  socketHandlers.get('match_compositions_by_mz')({
    status: 'success',
    data: {
      sample_item_id: 'si-1',
      mz: peak.mz,
      total: hits.length,
      results: hits.length,
      data: hits
    }
  })
  await wrapper.vm.$nextTick()
}

/** The hand buttons on the result rows - the only buttons inside the table. */
const handButtons = (wrapper) => wrapper.findAll('.dt-row button')

beforeEach(() => {
  // The pane's search parameters live in a Pinia store that persists overrides
  // to localStorage. A fresh Pinia and a cleared store per test keep a value
  // set in one from being the next one's starting point.
  localStorage.clear()
  setActivePinia(createPinia())
  helpMounted.length = 0
  helpUnmounted.length = 0
  socketHandlers = new Map()
  focusedPeak = ref(PEAK_A)
  ledger = new Map([
    [String(PEAK_A.peak_id), assignmentRow(PEAK_A, 'pa-a')],
    [String(PEAK_B.peak_id), assignmentRow(PEAK_B, 'pa-b')]
  ])
})
afterEach(() => vi.clearAllMocks())

describe('PanePeakSearch assigning a hit by hand', () => {
  it('commits the hit onto the focused peak ledger row', async () => {
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [hit('C6H12O6')])

    expect(handButtons(wrapper)).toHaveLength(1)
    await handButtons(wrapper)[0].trigger('click')

    expect(curate).toHaveBeenCalledTimes(1)
    expect(curate.mock.calls[0][0]).toBe('pa-a')
    expect(curate.mock.calls[0][1]).toMatchObject({
      action: 'set_assignment',
      assigned_formula: 'C6H12O6',
      ionization_mechanism_id: 'mech-1'
    })
  })

  it('disables the control when no run covers the peak, and says so', async () => {
    ledger.delete(String(PEAK_A.peak_id))
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [hit('C6H12O6')])

    expect(handButtons(wrapper)[0].attributes('disabled')).toBeDefined()
    expect(wrapper.vm.assignTooltip).toBe(
      'No assignment run covers this peak yet - assign the sample first'
    )

    await handButtons(wrapper)[0].trigger('click')
    expect(curate).not.toHaveBeenCalled()
  })

  it('hides the control when the write comes back 403', async () => {
    curate.mockRejectedValueOnce(Object.assign(new Error('no'), { response: { status: 403 } }))
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [hit('C6H12O6')])

    await handButtons(wrapper)[0].trigger('click')
    await wrapper.vm.$nextTick()

    expect(handButtons(wrapper)).toHaveLength(0)
  })

  // Any other failure has already been toasted by the http layer; the control
  // stays, because the user may well be able to retry.
  it('keeps the control after a failure that is not a refusal', async () => {
    curate.mockRejectedValueOnce(new Error('503 Service Unavailable'))
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [hit('C6H12O6')])

    await handButtons(wrapper)[0].trigger('click')
    await wrapper.vm.$nextTick()

    expect(handButtons(wrapper)).toHaveLength(1)
  })

  // A formula with no adduct is half an assignment and the endpoint refuses it,
  // so there is no state in which this row could be committed.
  it('offers no control on a hit that names no ionization mechanism', async () => {
    const adductless = hit('C4H8N2O3')
    delete adductless.ionization_mechanism_id
    delete adductless.cheminfo.ionization_mechanism

    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [hit('C6H12O6'), adductless])

    expect(wrapper.findAll('.dt-row')).toHaveLength(2)
    expect(handButtons(wrapper)).toHaveLength(1)
  })
})

// The search is debounced, so clicking a peak does not clear the results it
// replaces until several hundred milliseconds later. Everything below happens
// inside that window: the rows on screen are peak A's while the ledger row the
// button aims at is already peak B's.
describe('PanePeakSearch stale results after the focus moves', () => {
  async function focusMovesAfterResults() {
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [hit('C6H12O6')])
    focusedPeak.value = PEAK_B
    await wrapper.vm.$nextTick()
    return wrapper
  }

  it('still shows the previous peak results - the debounce has not fired', async () => {
    const wrapper = await focusMovesAfterResults()

    expect(wrapper.findAll('.dt-row')).toHaveLength(1)
  })

  it('disables the control rather than writing peak A candidate onto peak B', async () => {
    const wrapper = await focusMovesAfterResults()

    expect(handButtons(wrapper)[0].attributes('disabled')).toBeDefined()
    expect(wrapper.vm.assignTooltip).toBe(
      'These results are for the previously selected peak - the search for this one is still coming'
    )
  })

  it('refuses the write even when the click beats the re-render', async () => {
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [hit('C6H12O6')])

    // Straight into the handler, standing in for a click already on its way
    // when the focus changed: `disabled` only lands on the next render.
    focusedPeak.value = PEAK_B
    await wrapper.vm.assignToPeak(hit('C6H12O6'))

    expect(curate).not.toHaveBeenCalled()
  })

  // The whole point of the guard: the peak that gets written must be the one
  // whose results are on screen, never merely the one focused now.
  it('writes again once the results catch up with the new peak', async () => {
    const wrapper = await focusMovesAfterResults()
    await deliverResults(wrapper, PEAK_B, [hit('C20H30N2')])

    await handButtons(wrapper)[0].trigger('click')

    expect(curate).toHaveBeenCalledTimes(1)
    expect(curate.mock.calls[0][0]).toBe('pa-b')
    expect(curate.mock.calls[0][1]).toMatchObject({ assigned_formula: 'C20H30N2' })
  })

  // A payload for a peak that is no longer focused is dropped by the socket
  // handler, so it must not stamp the table with that peak either.
  it('ignores a late payload for a peak that is no longer focused', async () => {
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [hit('C6H12O6')])
    focusedPeak.value = PEAK_B
    await wrapper.vm.$nextTick()

    await deliverResults(wrapper, PEAK_A, [hit('C9H8O4')])

    expect(wrapper.vm.resultsPeakId).toBe(PEAK_A.peak_id)
    expect(handButtons(wrapper)[0].attributes('disabled')).toBeDefined()
  })
})

// A candidate found at another line of its ion than the monoisotopic one - its
// 13C line, a dibromide's brightest line - carries a tag naming that line,
// because the row otherwise reads as the compound's own mass on a peak it is not.
describe('PanePeakSearch results read at another line', () => {
  /** A hit whose pattern puts its 13C line on the searched peak. */
  function isotopologueHit(formula, mechanism = 'mech-1') {
    const found = hit(formula, mechanism)
    found.children = [
      { mz: 199.12, relative_abundance: 1.0, target_isotope_formula: formula },
      { mz: 200.1234, relative_abundance: 0.11, target_isotope_formula: `[13C]${formula}` }
    ]
    return found
  }

  it('tags a candidate found at its 13C line with that line', async () => {
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [hit('C6H12O6'), isotopologueHit('C9H15O4')])

    const tags = wrapper.findAll('.dt-row .line-tag')
    expect(tags).toHaveLength(1)
    expect(tags[0].text()).toBe('[13C]')
  })

  // One composition read under two adducts - at its monoisotopic line under
  // one, at an isotopologue line under the other - is two rows, and a row key
  // of the formula alone would make them one.
  it('keys a composition found under two adducts as two rows', async () => {
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [hit('C9H15O4'), isotopologueHit('C9H15O4', 'mech-2')])

    expect(wrapper.findAll('.dt-row')).toHaveLength(2)
    expect(wrapper.find('.dt').attributes('datakey')).toBe('key')
    expect(new Set(wrapper.vm.results.map((row) => row.key)).size).toBe(2)
  })

  it('commits the peak as that isotopologue of the compound', async () => {
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [isotopologueHit('C9H15O4')])

    await handButtons(wrapper)[0].trigger('click')

    expect(curate.mock.calls[0][1]).toMatchObject({
      assigned_formula: 'C9H15O4',
      isotope_label: 'M+1'
    })
  })
})

// An expanded candidate's isotope lines are rows of the table, under it: one
// fixed-height row each, which is what lets the table keep scrolling virtually
// with a long pattern open. A pattern nested in an expansion row was as tall as
// the pattern, the virtual scroller sliced past the candidate while it was
// being read, and the view snapped back to the top.
describe('PanePeakSearch isotope lines under an expanded candidate', () => {
  /** A hit with a three-line pattern, its lines out of m/z order. */
  function patternHit(formula, fit) {
    const found = hit(formula)
    found.fit_score = fit
    found.children = [
      { mz: 201.1268, relative_abundance: 0.11, target_isotope_formula: `[13C]${formula}` },
      { mz: 200.1234, relative_abundance: 1.0, target_isotope_formula: formula },
      { mz: 202.1302, relative_abundance: 0.02, target_isotope_formula: `[18O]${formula}` }
    ]
    return found
  }

  const expand = async (wrapper, formula) => {
    const { key } = wrapper.vm.results.find((row) => row.target_compound_formula === formula)
    wrapper.vm.expanded = { [key]: true }
    await wrapper.vm.$nextTick()
  }

  // What each row is: a candidate's formula, or the m/z of a line under one.
  const rowNames = (wrapper) =>
    wrapper.vm.rows.map((row) => (row.parent ? row.mz : row.target_compound_formula))

  it("lists an expanded candidate's lines under it, lightest first", async () => {
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [patternHit('C6H12O6', 0.9), patternHit('C9H8O4', 0.5)])

    await expand(wrapper, 'C6H12O6')

    expect(rowNames(wrapper)).toEqual(['C6H12O6', 200.1234, 201.1268, 202.1302, 'C9H8O4'])
    expect(wrapper.findAll('.dt-row')).toHaveLength(5)
    // A line is not a candidate: it has no hand button of its own.
    expect(handButtons(wrapper)).toHaveLength(2)
  })

  it('keeps the lines under their candidate whatever the sort', async () => {
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [patternHit('C9H8O4', 0.9), patternHit('C6H12O6', 0.5)])
    await expand(wrapper, 'C9H8O4')

    wrapper.vm.sortField = 'target_compound_formula'
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()

    expect(rowNames(wrapper)).toEqual(['C6H12O6', 'C9H8O4', 200.1234, 201.1268, 202.1302])
  })

  it('previews a line in the spectrum on a click, and takes it away on a second', async () => {
    const { usePreview } = await import('@/lib/panes/PanePeakAssign/preview.js')
    const preview = usePreview()
    preview.peak = null
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [patternHit('C6H12O6', 0.9)])
    await expand(wrapper, 'C6H12O6')

    const line = () => wrapper.findAll('.dt-row')[2]
    await line().trigger('click')
    expect(preview.peak).toMatchObject({ mz: 201.1268, relative_abundance: 0.11 })

    await line().trigger('click')
    expect(preview.peak).toBeNull()

    // A candidate's row is not a line, and previews nothing.
    await wrapper.findAll('.dt-row')[0].trigger('click')
    expect(preview.peak).toBeNull()
  })
})

// The hand button's help card is anchored on the column header rather than on
// the button, because a card registered inside a virtual-scrolled row body
// would leak one per row rendered. The header carries the same hazard in
// miniature: the glyph it used to hang on disappears when a write comes back
// 403, and the card registered on it would then be stranded - present in the
// store's list, attached to an element no longer in the document, for the rest
// of the session.
describe('PanePeakSearch curation help card', () => {
  const glyph = (wrapper) => wrapper.find('.dt-head .ph-hand-pointing')

  it('registers one card on the results header', async () => {
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [hit('C6H12O6')])

    expect(curationCards(helpMounted)).toHaveLength(1)
    expect(glyph(wrapper).exists()).toBe(true)
  })

  it('keeps the card mounted when the write comes back 403', async () => {
    curate.mockRejectedValueOnce(Object.assign(new Error('no'), { response: { status: 403 } }))
    const wrapper = await mountPane()
    await deliverResults(wrapper, PEAK_A, [hit('C6H12O6')])

    await handButtons(wrapper)[0].trigger('click')
    await wrapper.vm.$nextTick()

    // The control and the glyph that explains it still go - a viewer who cannot
    // curate is shown neither the button nor the help for it.
    expect(handButtons(wrapper)).toHaveLength(0)
    expect(glyph(wrapper).exists()).toBe(false)
    // But the element the card sits on stays, because nothing would ever
    // unregister the card if it went.
    expect(curationCards(helpUnmounted)).toHaveLength(0)
    expect(wrapper.find('.dt-head .curate-header').exists()).toBe(true)
  })
})
