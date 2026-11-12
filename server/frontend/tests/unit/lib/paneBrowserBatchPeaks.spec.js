import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

import { MAX_SELECTED_BATCH_PEAKS } from '@/stores/data/modules/batchPeak/ledger'
import { TIERING_PROVISIONAL } from '@/lib/tiers'

// The tier strip and the tier column's sort: both read the confidence order out
// of @/lib/tiers, and both used to be wrong in the same direction (alphabetical,
// worst tier first). The button that builds the batch peaks is no longer here -
// it moved to the browser's switch bar, and its own spec is
// panes/PaneBrowserMatch/batchPeakComputeBar.spec.js - but the refusal it can
// come back with is still rendered below this table, so what that leaves behind
// is covered under "compute refusal" below.

const post = vi.fn()

vi.mock('@/api', () => ({ api: { http: { post: (...args) => post(...args) } } }))

let app
// Callbacks the compute store registered through app.ui.notification.on, by
// type. The pane registers none of its own any more; the entry is kept because
// mounting it creates that store, which does.
let notificationHandlers

vi.mock('@/stores', () => ({ useApp: () => app }))

vi.mock('@/lib/base', async () => ({
  BaseTabbedPanel: { template: '<div><slot name="menu" /><slot /></div>' },
  // Rendered, so a test that renders the cells can read the chip's role and
  // the formula a cell would copy.
  BaseTierTag: {
    name: 'BaseTierTag',
    props: ['tier', 'role'],
    template: '<span class="tier-tag" :data-tier="tier" :data-role="role" />'
  },
  BaseCopyableField: {
    props: ['field', 'tooltip'],
    template: '<span class="copyable" :data-tooltip="tooltip">{{ field }}<slot /></span>'
  },
  BaseVerdictBadge: true,
  // Real: the tier header's provisional mark is read off what it draws.
  BaseProvisionalMark: (await vi.importActual('@/lib/base/BaseProvisionalMark.vue')).default
}))
vi.mock('@/lib/panes/PaneBrowserMatch/BatchPeakVerdictPopover.vue', () => ({
  default: { name: 'BatchPeakVerdictPopover', template: '<div class="verdict-popover-stub" />' }
}))

// Minimal help-mode facade: the pane registers help cards through these calls;
// the tests only need them to resolve.
const helpStub = {
  set: vi.fn(),
  docUrl: (path = '') => `/docs/${path}`,
  directive: () => ({}),
  right: () => ({}),
  left: () => ({}),
  top: () => ({}),
  bottom: () => ({})
}

const BATCH = { sample_batch_id: 'b-1', sample_batch_name: 'Batch 1' }
const WORKSPACE = { workspace_id: 'ws-1', workspace_name: 'Project', my_role: 'editor' }

/** A batch-peak ledger row. */
const peak = (batch_peak_id, consensus_tier, best_fit_score, extra = {}) => ({
  batch_peak_id,
  mz: 181.0708,
  consensus_formula: 'C6H12O6',
  consensus_tier,
  best_fit_score,
  n_present: 2,
  ...extra
})

function makeApp({
  batch = BATCH,
  workspace = WORKSPACE,
  samples = [{}],
  peaks = [],
  verdicts = {},
  ledger = {}
} = {}) {
  return reactive({
    auth: { user: { role_id: 200, is_superuser: false } },
    data: {
      batch: { focused: batch, focusedId: batch?.sample_batch_id ?? null },
      workspace: { focused: workspace },
      sample: { list: samples, pending: false },
      batchPeak: {
        list: peaks,
        pending: false,
        error: null,
        selected: [],
        load: vi.fn(),
        // One instrument type: no partition to choose, every row shown.
        instrumentTypes: [],
        instrumentType: null,
        inPartition: () => true,
        showInstrument: vi.fn(),
        ...ledger
      },
      // Batch-level verdicts by anchor id; stale when the record's formula is
      // not the row's, as the real store decides.
      batchPeakVerification: {
        list: Object.values(verdicts),
        forAnchor: (row) => verdicts[row.batch_peak_id] ?? null,
        isStale: (record, row) => record.assigned_formula !== row.consensus_formula
      },
      batchPeakRun: {
        list: [],
        focused: null,
        viewing: null,
        viewingCurrent: true,
        viewingId: null
      }
    },
    ui: {
      help: helpStub,
      notification: {
        on: (type, callback) => {
          notificationHandlers[type] = [...(notificationHandlers[type] ?? []), callback]
          return { remove: () => {} }
        },
        push: vi.fn()
      }
    }
  })
}

// The table is stubbed, but not away: `DataTable: true` renders no default
// slot, so the columns are never instantiated and the props the tier column
// hands PrimeVue - the sort field the header click orders by, and the filter
// binding the chips write into - go untested. Reverting either would leave the
// suite green. So the stub renders its slot, and the column records what it
// was given.
const ColumnStub = {
  name: 'Column',
  // Typed as PrimeVue types them, so a bare `sortable` casts to true rather
  // than the empty string. The booleans default to null instead of false: an
  // absent prop must not read as one deliberately bound false.
  props: {
    field: String,
    sortField: String,
    sortable: Boolean,
    header: String,
    filterField: String,
    maxConstraints: Number,
    showAddButton: { type: Boolean, default: null },
    showOperator: { type: Boolean, default: null }
  },
  template: '<div class="column-stub" />'
}

// The table is where every bulk selection is made, so the stub has to be able
// to make one: it declares the selection props the pane binds and the two
// events it answers, so a test can select the way the header checkbox and a
// shift-click range each do. `keydown` is left undeclared on purpose - it falls
// through to the root element, which is how a test reaches Ctrl+A.
// It deliberately does NOT model a filtered view of the rows. The table is
// `lazy`, so filtering is the pane's: what it hands the table is already the
// filtered, sorted, folded array, and a stub that could narrow it further would
// be modelling a step that no longer exists - and the tests that drove it would
// pass without ever touching the real filter.
const DataTableStub = {
  name: 'DataTable',
  props: ['value', 'filters', 'scrollHeight', 'selection', 'selectAll'],
  emits: ['update:selection', 'select-all-change'],
  template: '<div class="datatable"><slot /></div>'
}

// Rendered rather than stubbed away, so the toggle a test flips is the one the
// pane binds: `ToggleSwitch: true` would render an element with no v-model.
const ToggleSwitchStub = {
  name: 'ToggleSwitch',
  props: ['modelValue', 'inputId'],
  emits: ['update:modelValue'],
  template:
    '<button class="iso-toggle" :id="inputId" @click="$emit(\'update:modelValue\', !modelValue)">{{ modelValue }}</button>'
}

// Rendered open, rather than stubbed away: the view menu's contents are the
// point of the menu, and `Popover: true` would render no slot at all - so the
// isotopologue toggle inside it would not exist to flip. Its own open/close is
// PrimeVue's; what this file tests is that the toggle lives in there.
const PopoverStub = {
  name: 'Popover',
  template: '<div class="view-popover"><slot /></div>'
}

const GLOBAL_STUBS = {
  Button: {
    props: ['label', 'disabled', 'loading', 'icon'],
    template:
      '<button class="menu-button" :disabled="disabled" :data-icon="icon" :data-loading="String(!!loading)">{{ label }}</button>'
  },
  Message: { template: '<div class="pane-message"><slot /></div>' },
  DataTable: DataTableStub,
  Column: ColumnStub,
  Popover: PopoverStub,
  ToggleSwitch: ToggleSwitchStub,
  InputText: true,
  Select: true,
  SelectButton: {
    name: 'SelectButton',
    props: ['modelValue', 'options'],
    emits: ['update:modelValue'],
    template: '<div class="select-button" />'
  }
}

// Imported statically (vi.mock is hoisted above it, so the mocks still apply):
// compiling this pane is the expensive part, and as a dynamic import inside the
// first test it lands on that one test's clock.
const { default: PaneBrowserBatchPeaks } =
  await import('@/lib/panes/PaneBrowserMatch/PaneBrowserBatchPeaks.vue')
const { useBatchPeakCompute } =
  await import('@/lib/panes/PaneBrowserMatch/stores/batchPeakCompute.js')

async function mountPane(options = {}) {
  // The app facade first: the compute store the pane reads its refusal off is
  // created during that mount, and it reads the facade while being created.
  app = makeApp(options)
  // The ledger's sort store writes through to localStorage and reads it back on
  // creation, so without this a sort set in one test would be the next test's
  // starting order.
  localStorage.clear()
  setActivePinia(createPinia())
  const wrapper = mount(PaneBrowserBatchPeaks, {
    global: {
      stubs: GLOBAL_STUBS,
      directives: { tooltip: {}, help: {} }
    }
  })
  await wrapper.vm.$nextTick()
  return wrapper
}

/** An axios-shaped rejection carrying a server message. */
const apiError = (status, message) => ({ response: { status, data: { error: message } } })

/** The rendered column bound to `field`. */
const columnFor = (field) =>
  wrapper.findAllComponents(ColumnStub).find((column) => column.props('field') === field)

/** A 202 acknowledgement, with the process id on the header the route sets. */
const ack = (processId = 'proc-1') => ({ headers: { 'process-id': processId } })

let wrapper

beforeEach(() => {
  vi.useFakeTimers()
  notificationHandlers = {}
  post.mockReset()
  post.mockResolvedValue(ack())
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('PaneBrowserBatchPeaks compute refusal', () => {
  // The button is a row up, in the switch bar, but the refusal it comes back
  // with is reported here - below the table it is about, rather than in a toast
  // that has scrolled away by the time the user looks up.
  it('reports a refused launch below the table', async () => {
    post.mockRejectedValue(apiError(403, 'You do not have permission.'))
    wrapper = await mountPane()

    await useBatchPeakCompute().compute()
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.pane-message')).toHaveLength(1)
    expect(wrapper.find('.pane-message').text()).toContain('do not have permission')
  })

  // Whatever was in flight or went wrong belonged to the previous batch. The
  // pane owns this because it owns the rest of the per-batch reset beside it.
  it('clears the launch state when the focused batch changes', async () => {
    post.mockRejectedValue(apiError(500, 'boom'))
    wrapper = await mountPane()
    const compute = useBatchPeakCompute()

    await compute.compute()
    await wrapper.vm.$nextTick()
    expect(wrapper.findAll('.pane-message')).toHaveLength(1)

    app.data.batch.focusedId = 'b-2'
    await wrapper.vm.$nextTick()

    expect(compute.launchError).toBeNull()
    expect(compute.computing).toBe(false)
    expect(wrapper.findAll('.pane-message')).toHaveLength(0)
  })
})

describe('PaneBrowserBatchPeaks tier strip', () => {
  const peaks = [
    peak('bp-1', 'assigned', 0.9),
    peak('bp-2', 'assigned', 0.7),
    peak('bp-3', 'candidate', 0.5),
    peak('bp-4', 'unassigned', null)
  ]

  it('renders one chip per tier in confidence order, then one per role, with counts', async () => {
    wrapper = await mountPane({ peaks })
    const chips = wrapper.findAll('.tier-stat')

    expect(chips.map((chip) => chip.text())).toEqual([
      '2 assigned',
      '1 candidate',
      '0 below',
      '1 unassigned',
      '0 reagent',
      '0 artifact'
    ])
    // Set off from the tiers: what follows is not a confidence.
    expect(chips[4].classes()).toContain('roles-start')
  })

  // Step 3.3c's follow-up, built in 3.4d: an anchor whose members the reagent
  // pre-pass claimed carries the role and the ion, and counts under its role
  // rather than as a peak nothing explained.
  it('counts an anchor a source role accounts for under its role', async () => {
    wrapper = await mountPane({
      peaks: [
        ...peaks,
        peak('bp-br', 'unassigned', null, {
          mz: 78.9189,
          consensus_formula: null,
          consensus_role: 'reagent',
          consensus_ion_formula: 'Br-'
        }),
        peak('bp-ring', 'unassigned', null, {
          mz: 400.1,
          consensus_formula: null,
          consensus_role: 'artifact'
        })
      ]
    })
    const counts = wrapper.findAll('.tier-stat').map((chip) => chip.text())

    expect(counts).toContain('1 unassigned')
    expect(counts).toContain('1 reagent')
    expect(counts).toContain('1 artifact')

    await wrapper.findAll('.tier-stat')[4].trigger('click')
    expect(wrapper.vm.filters.consensus_tier.constraints[0].value).toBe('reagent')
    expect(wrapper.vm.rows.map((row) => row.batch_peak_id)).toEqual(['bp-br'])
  })

  it('filters the table by writing the tier filter the column menu reads', async () => {
    wrapper = await mountPane({ peaks })

    await wrapper.findAll('.tier-stat')[1].trigger('click')

    expect(wrapper.vm.activeTier).toBe('candidate')
    // The same constraint the filter menu binds to, so the two cannot disagree.
    expect(wrapper.vm.filters.consensus_tier.constraints[0].value).toBe('candidate')
  })

  it('clears the filter when the active chip is clicked again', async () => {
    wrapper = await mountPane({ peaks })
    const candidate = wrapper.findAll('.tier-stat')[1]

    await candidate.trigger('click')
    await candidate.trigger('click')

    expect(wrapper.vm.activeTier).toBeNull()
    expect(wrapper.vm.filters.consensus_tier.constraints[0].value).toBeNull()
  })

  it('counts species rather than anchors, so a chip narrows to the number it shows', async () => {
    // A chip is a filter control: the number on it is a promise about how many
    // rows clicking it produces. Counting the isotopologues too would promise three
    // assigned rows and deliver one.
    wrapper = await mountPane({
      peaks: [
        peak('bp-m0', 'assigned', 0.9, { mz: 181.07 }),
        peak('bp-iso-1', 'assigned', 0.9, { mz: 182.07, isotopologue_of: 'bp-m0' }),
        peak('bp-iso-2', 'assigned', 0.9, { mz: 183.07, isotopologue_of: 'bp-m0' })
      ]
    })

    expect(wrapper.findAll('.tier-stat')[0].text()).toBe('1 assigned')

    await wrapper.findAll('.tier-stat')[0].trigger('click')
    expect(wrapper.vm.rows).toHaveLength(1)
  })

  it('marks the active chip and dims the rest', async () => {
    wrapper = await mountPane({ peaks })
    await wrapper.findAll('.tier-stat')[0].trigger('click')

    const classes = wrapper.findAll('.tier-stat').map((chip) => chip.classes())
    expect(classes[0]).toContain('active')
    expect(classes[0]).not.toContain('dim')
    expect(classes[1]).toContain('dim')
  })
})

// The cells of an anchor a source role accounts for: the ion in the formula
// column, the role as the chip, and the tier column sorting it after the tiers.
describe('PaneBrowserBatchPeaks reagent anchors', () => {
  const REAGENT = peak('bp-br', 'unassigned', null, {
    mz: 78.9189,
    consensus_formula: null,
    consensus_role: 'reagent',
    consensus_ion_formula: 'Br-'
  })
  const ARTIFACT = peak('bp-ring', 'unassigned', null, {
    mz: 400.1,
    consensus_formula: null,
    consensus_role: 'artifact'
  })
  const BARE = peak('bp-bare', 'unassigned', null, { mz: 250.1, consensus_formula: null })

  async function rendered(peaks) {
    const tableRows = ref([])
    app = makeApp({ peaks })
    localStorage.clear()
    setActivePinia(createPinia())
    const mounted = mount(PaneBrowserBatchPeaks, {
      global: {
        stubs: {
          ...GLOBAL_STUBS,
          DataTable: {
            ...DataTableStub,
            watch: {
              value: { handler: (value) => (tableRows.value = value), immediate: true }
            }
          },
          Column: {
            ...ColumnStub,
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="column-stub" :data-field="field">' +
              '<template v-for="(row, i) in rows" :key="i">' +
              '<div class="stub-cell"><slot name="body" :data="row" /></div></template></div>'
          }
        },
        directives: { tooltip: {}, help: {} }
      }
    })
    await mounted.vm.$nextTick()
    return mounted
  }
  const cells = (field) =>
    wrapper
      .findAll('.column-stub')
      .find((column) => column.attributes('data-field') === field)
      .findAll('.stub-cell')

  it("shows a reagent anchor's ion where an analyte's formula would be", async () => {
    wrapper = await rendered([REAGENT, ARTIFACT, BARE])
    const byId = Object.fromEntries(
      wrapper.vm.rows.map((row, index) => [row.batch_peak_id, cells('consensus_formula')[index]])
    )

    expect(byId['bp-br'].find('.copyable').text()).toBe('Br-')
    expect(byId['bp-br'].find('.copyable').attributes('data-tooltip')).toMatch(/Ion formula/)
    // An artifact names no ion, and its chip says what it is: a dash, not the
    // "unassigned" a peak nothing explained reads.
    expect(byId['bp-ring'].text()).toBe('—')
    expect(byId['bp-bare'].text()).toBe('unassigned')
  })

  it('hands the role to the chip', async () => {
    wrapper = await rendered([REAGENT, BARE])
    const byId = Object.fromEntries(
      wrapper.vm.rows.map((row, index) => [
        row.batch_peak_id,
        cells('consensus_tier')[index].find('.tier-tag')
      ])
    )

    expect(byId['bp-br'].attributes('data-role')).toBe('reagent')
    expect(byId['bp-br'].attributes('data-tier')).toBe('unassigned')
    expect(byId['bp-bare'].attributes('data-role')).toBeUndefined()
  })

  it('sorts the roles after the tiers, in the strip order', async () => {
    wrapper = await mountPane({ peaks: [ARTIFACT, REAGENT, BARE, peak('bp-a', 'assigned', 0.9)] })

    expect(wrapper.vm.rows.map((row) => row.batch_peak_id)).toEqual([
      'bp-a',
      'bp-bare',
      'bp-br',
      'bp-ring'
    ])
  })

  it('finds the ion with the formula search', async () => {
    wrapper = await mountPane({ peaks: [REAGENT, peak('bp-a', 'assigned', 0.9)] })

    wrapper.vm.filters.consensus_formula.constraints[0].value = 'Br'
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.rows.map((row) => row.batch_peak_id)).toEqual(['bp-br'])
  })

  it('folds a reagent line under its ion, as an isotopologue folds', async () => {
    const line = peak('bp-81br', 'unassigned', null, {
      mz: 80.9168,
      consensus_formula: null,
      consensus_role: 'reagent',
      consensus_ion_formula: 'Br-',
      isotopologue_of: 'bp-br'
    })
    wrapper = await mountPane({ peaks: [REAGENT, line] })

    expect(wrapper.vm.rows.map((row) => row.batch_peak_id)).toEqual(['bp-br'])
    // One reagent species, its line folded into it.
    expect(wrapper.findAll('.tier-stat').map((chip) => chip.text())).toContain('1 reagent')
  })
})

describe('PaneBrowserBatchPeaks tier ordering', () => {
  it('carries a confidence rank on every row for the tier column to sort on', async () => {
    wrapper = await mountPane({
      peaks: [peak('bp-1', 'unassigned', null), peak('bp-2', 'assigned', 0.9)]
    })

    expect(wrapper.vm.rows.map((row) => row.tierRank)).toEqual([0, 3])
  })

  it('orders assigned before candidate before below before unassigned', async () => {
    wrapper = await mountPane({
      peaks: [
        peak('bp-1', 'unassigned', null),
        peak('bp-2', 'below_assignability', 0.2),
        peak('bp-3', 'assigned', 0.9),
        peak('bp-4', 'candidate', 0.5)
      ]
    })

    expect(wrapper.vm.rows.map((row) => row.consensus_tier)).toEqual([
      'assigned',
      'candidate',
      'below_assignability',
      'unassigned'
    ])
  })

  it('breaks a tier tie by fit, the percentage the chip beside it shows', async () => {
    wrapper = await mountPane({
      peaks: [
        peak('bp-low', 'assigned', 0.61),
        peak('bp-high', 'assigned', 0.98),
        peak('bp-none', 'assigned', null)
      ]
    })

    expect(wrapper.vm.rows.map((row) => row.batch_peak_id)).toEqual([
      'bp-high',
      'bp-low',
      'bp-none'
    ])
  })

  it('ranks a row with no tier last rather than first', async () => {
    wrapper = await mountPane({
      peaks: [peak('bp-1', null, null), peak('bp-2', 'candidate', 0.5)]
    })

    expect(wrapper.vm.rows.map((row) => row.batch_peak_id)).toEqual(['bp-2', 'bp-1'])
  })

  // The sort lives in a store outside the pane: PaneBrowserMatch swaps this pane
  // for the sample ledger's whenever a sample is focused, and a sort held in the
  // pane's own state went with it. A second mount on the same Pinia is that
  // switch and back - mountPane would start a fresh Pinia, which is the reload
  // case the store's own spec covers.
  it('keeps its sort across a remount', async () => {
    const first = await mountPane()
    first.vm.sortField = 'mz'
    first.vm.sortOrder = 1
    await first.vm.$nextTick()
    first.unmount()

    const second = mount(PaneBrowserBatchPeaks, {
      global: { stubs: GLOBAL_STUBS, directives: { tooltip: {}, help: {} } }
    })
    await second.vm.$nextTick()
    expect(second.vm.sortField).toBe('mz')
    expect(second.vm.sortOrder).toBe(1)
    const mzs = second.vm.rows.filter((row) => !row.parentId).map((row) => row.mz)
    expect(mzs).toEqual([...mzs].sort((a, b) => a - b))
  })

  // Every header but the selection checkbox's explains its column on hover: the
  // numbers here are properties of the batch peak, where the same column names
  // in the sample ledger mean one peak's. The directive records what it was
  // given, and the Column stub renders the header slot the spans live in - a
  // tooltip placed on the Column itself would never show, since Column renders
  // no element of its own.
  it('explains every column header on hover', async () => {
    app = makeApp()
    localStorage.clear()
    setActivePinia(createPinia())
    const wrapper = mount(PaneBrowserBatchPeaks, {
      global: {
        stubs: {
          ...GLOBAL_STUBS,
          Column: {
            ...ColumnStub,
            template: '<div class="column-stub"><slot name="header" /></div>'
          }
        },
        directives: {
          tooltip: { mounted: (el, binding) => el.setAttribute('data-tooltip', binding.value) },
          help: {}
        }
      }
    })
    await wrapper.vm.$nextTick()

    const tips = wrapper
      .findAll('.column-stub [data-tooltip]')
      .map((span) => [span.text(), span.attributes('data-tooltip')])
    expect(tips.map(([label]) => label)).toEqual([
      'm/z',
      'Intensity',
      'Formula',
      '', // the listing header is a flask while the column is folded
      'Tier',
      // The tiering is still being built, and the tier header says so.
      TIERING_PROVISIONAL.label,
      'Samples',
      '' // the verdict header is an icon
    ])
    for (const [, tip] of tips) expect(tip.length).toBeGreaterThan(20)
    expect(tips.find(([label]) => label === TIERING_PROVISIONAL.label)[1]).toBe(
      TIERING_PROVISIONAL.tooltip
    )
    expect(tips.find(([label]) => label === 'm/z')[1]).toMatch(/anchor/i)
    expect(tips.find(([label]) => label === 'Intensity')[1]).toMatch(/any sample/)
    expect(tips.find(([label]) => label === 'Formula')[1]).toMatch(/consensus/i)
  })

  it('sorts the tier column on the rank, which is what the header click reads', async () => {
    // The pre-sorted `rows` above are only the tie-break; a header click sorts
    // on the column's own sort field, and the bug this fixes was that field
    // being the raw tier string.
    wrapper = await mountPane()

    expect(columnFor('consensus_tier').props('sortField')).toBe('tierRank')
    expect(columnFor('consensus_tier').props('sortable')).toBe(true)
  })

  it('orders by confidence when sorted the way the table sorts', async () => {
    // Ascending on the column's sort field, as PrimeVue does on the first
    // header click. On the raw tier string this reads
    // assigned, below_assignability, candidate, unassigned.
    wrapper = await mountPane({
      peaks: [
        peak('bp-1', 'unassigned', null),
        peak('bp-2', 'below_assignability', 0.2),
        peak('bp-3', 'assigned', 0.9),
        peak('bp-4', 'candidate', 0.5)
      ]
    })
    // Falling back to `field` the way PrimeVue does, so dropping the sortField
    // fails here rather than quietly sorting on nothing.
    const column = columnFor('consensus_tier')
    const sortField = column.props('sortField') ?? column.props('field')

    const ascending = [...wrapper.vm.rows].sort((a, b) =>
      a[sortField] < b[sortField] ? -1 : a[sortField] > b[sortField] ? 1 : 0
    )

    expect(ascending.map((row) => row.consensus_tier)).toEqual([
      'assigned',
      'candidate',
      'below_assignability',
      'unassigned'
    ])
  })

  it('keeps the tier filter bound to the column, which sorting elsewhere would break', async () => {
    // PrimeVue reads the filter menu's model out of `filters[field]` and
    // dereferences it unguarded, so the field has to stay the one the filters
    // object is keyed by - the reason the rank is a sortField and not the field.
    wrapper = await mountPane()

    expect(Object.keys(wrapper.vm.filters)).toContain(columnFor('consensus_tier').props('field'))
  })

  it('offers the tier menu one rule, so no rule can outlive the chips', async () => {
    // A second EQUALS rule ANDed onto the first matches nothing, and the strip
    // - which reads only the first - would go on claiming to show a tier.
    wrapper = await mountPane()

    expect(columnFor('consensus_tier').props('maxConstraints')).toBe(1)
    expect(columnFor('consensus_tier').props('showAddButton')).toBe(false)
  })
})

// The ledger lists every batch peak of the batch, singletons included, so "all"
// is a number that grows with the batch and nothing about the gesture warns the
// user how large it is. Everything downstream is priced per selected record, so
// the write into the selection is where the size has to be settled - and every
// route into it has to land in the same place, or the ones that do not become
// the way to get an unbounded selection anyway.
// What a reference list calls the consensus formula (step 3.4d), off the
// flattened field the ledger row carries.
describe('PaneBrowserBatchPeaks reference-list column', () => {
  const LISTING = {
    name: 'decamethylcyclopentasiloxane',
    source: 'cyclic-siloxanes',
    tags: ['background'],
    total: 2
  }

  // Renders the cells: the stub table hands its rows to every column through a
  // ref, as the sample ledger's spec does.
  async function renderedCells(peaks, { full = false } = {}) {
    const tableRows = ref([])
    app = makeApp({ peaks })
    localStorage.clear()
    if (full) localStorage.setItem('mascope.browser.match.ledgerListing', '{"full":true}')
    setActivePinia(createPinia())
    const rendered = mount(PaneBrowserBatchPeaks, {
      global: {
        stubs: {
          ...GLOBAL_STUBS,
          DataTable: {
            ...DataTableStub,
            watch: {
              value: { handler: (value) => (tableRows.value = value), immediate: true }
            }
          },
          Column: {
            ...ColumnStub,
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="column-stub" :data-field="field"><slot name="header" />' +
              '<template v-for="(row, i) in rows" :key="i">' +
              '<div class="stub-cell"><slot name="body" :data="row" /></div></template></div>'
          }
        },
        directives: { tooltip: {}, help: {} }
      }
    })
    await rendered.vm.$nextTick()
    return rendered
  }

  const listingColumn = () =>
    wrapper.findAll('.column-stub').find((col) => col.attributes('data-field') === 'listingName')

  it('folds to a flask and the tags by default', async () => {
    wrapper = await renderedCells([
      peak('bp-1', 'assigned', 0.9, { reference_listing: LISTING }),
      peak('bp-2', 'assigned', 0.8, { mz: 200.1 })
    ])
    const [listed, unlisted] = listingColumn().findAll('.stub-cell')

    expect(listingColumn().find('[aria-label="Listed as"]').exists()).toBe(true)
    expect(listed.find('.listing-name').exists()).toBe(false)
    expect(listed.find('.listing-mark').attributes('aria-label')).toBe(
      'decamethylcyclopentasiloxane +1'
    )
    expect(listed.find('[data-testid="list-tag-background"]').exists()).toBe(true)
    expect(unlisted.text()).toBe('')
  })

  it("writes the column out from the view menu's switch", async () => {
    wrapper = await renderedCells([peak('bp-1', 'assigned', 0.9, { reference_listing: LISTING })])
    const toggle = wrapper.find('#batch-listing-full')

    expect(toggle.text()).toBe('false')
    await toggle.trigger('click')
    const listed = listingColumn().find('.stub-cell')
    expect(listed.find('.listing-name').text()).toBe('decamethylcyclopentasiloxane +1')
    expect(listingColumn().text()).toContain('Listed as')
    expect(JSON.parse(localStorage.getItem('mascope.browser.match.ledgerListing'))).toEqual({
      full: true
    })
  })

  it('shows the name, the list and its tag on a listed batch peak, in full', async () => {
    wrapper = await renderedCells(
      [
        peak('bp-1', 'assigned', 0.9, { reference_listing: LISTING }),
        peak('bp-2', 'assigned', 0.8, { mz: 200.1 })
      ],
      { full: true }
    )
    const column = wrapper
      .findAll('.column-stub')
      .find((col) => col.attributes('data-field') === 'listingName')
    const [listed, unlisted] = column.findAll('.stub-cell')

    expect(listed.find('.listing-name').text()).toBe('decamethylcyclopentasiloxane +1')
    expect(listed.find('.listing-source').text()).toBe('cyclic-siloxanes')
    expect(listed.find('[data-testid="list-tag-background"]').exists()).toBe(true)
    expect(unlisted.text()).toBe('')
  })

  it('reads the listing onto the row, and sorts on its name', async () => {
    wrapper = await mountPane({
      peaks: [
        peak('bp-1', 'assigned', 0.9, { reference_listing: LISTING }),
        peak('bp-2', 'assigned', 0.8, { mz: 200.1 })
      ]
    })
    const rows = new Map(wrapper.vm.rows.map((row) => [row.batch_peak_id, row]))

    expect(rows.get('bp-1').listingName).toBe('decamethylcyclopentasiloxane +1')
    expect(rows.get('bp-2').listingName).toBeNull()
    expect(columnFor('listingName').props('sortable')).toBe(true)
  })

  // What the target the consensus was matched to declares (identification
  // claims, section 4.2), off the anchor's consensus as the sample ledger reads
  // a row's.
  const GLUCOSE = 'WQZGKKKJIJFFOK-GASJEMHNSA-N'
  const TARGETED = {
    name: null,
    source: null,
    inchikey: null,
    tags: [],
    total: 0,
    target: { label: 'target', name: 'glucose', inchikey: GLUCOSE, listed: false }
  }

  it("reads the consensus's target as target and its name, and sorts on it", async () => {
    wrapper = await renderedCells(
      [
        peak('bp-1', 'assigned', 0.9, { reference_listing: TARGETED }),
        peak('bp-2', 'assigned', 0.8, { mz: 200.1 })
      ],
      { full: true }
    )
    const [listed, unlisted] = listingColumn().findAll('.stub-cell')

    expect(listed.find('[data-testid="target-as"]').text()).toBe('target')
    expect(listed.find('.listing-name').text()).toBe('glucose')
    expect(listed.find('[data-testid="target-unlisted"]').exists()).toBe(true)
    expect(unlisted.text()).toBe('')
    const rows = new Map(wrapper.vm.rows.map((row) => [row.batch_peak_id, row]))
    expect(rows.get('bp-1').listingName).toBe('glucose')
  })

  it('shows a target a list names by the same structure once, with both labels', async () => {
    const flat = {
      ...LISTING,
      inchikey: GLUCOSE,
      target: { ...TARGETED.target, listed: true }
    }
    wrapper = await renderedCells([peak('bp-1', 'assigned', 0.9, { reference_listing: flat })], {
      full: true
    })
    const listed = listingColumn().find('.stub-cell')

    expect(listed.findAll('.listing-name').map((name) => name.text())).toEqual(['glucose +1'])
    expect(listed.find('.listing-source').text()).toBe('cyclic-siloxanes')
    expect(listed.find('[data-testid="target-unlisted"]').exists()).toBe(false)
  })

  it('folds the target to its mark', async () => {
    wrapper = await renderedCells([peak('bp-1', 'assigned', 0.9, { reference_listing: TARGETED })])
    const listed = listingColumn().find('.stub-cell')

    expect(listed.find('[data-testid="target-as"]').attributes('aria-label')).toBe(
      'target: glucose'
    )
    expect(listed.find('.ph-flask').exists()).toBe(false)
  })
})

describe('PaneBrowserBatchPeaks series mark', () => {
  const SERIES = {
    formula: 'C6H12O6',
    unit: 'CH2',
    units: ['CH2'],
    step: 1,
    depth: 1,
    root_formula: 'C5H10O6',
    has_anchor: true
  }

  // Renders the formula cells through a column stub that hands each its row.
  async function formulaCells(peaks) {
    const tableRows = ref([])
    app = makeApp({ peaks })
    localStorage.clear()
    setActivePinia(createPinia())
    wrapper = mount(PaneBrowserBatchPeaks, {
      global: {
        stubs: {
          ...GLOBAL_STUBS,
          DataTable: {
            ...DataTableStub,
            watch: {
              value: { handler: (value) => (tableRows.value = value), immediate: true }
            }
          },
          Column: {
            ...ColumnStub,
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="column-stub" :data-field="field">' +
              '<template v-for="(row, i) in rows" :key="i">' +
              '<div class="stub-cell"><slot name="body" :data="row" /></div></template></div>'
          }
        },
        directives: { tooltip: {}, help: {} }
      }
    })
    await wrapper.vm.$nextTick()
    return wrapper
      .findAll('.column-stub')
      .find((col) => col.attributes('data-field') === 'consensus_formula')
      .findAll('.stub-cell')
  }

  it('marks a row a series vouches for, and says which', async () => {
    const cells = await formulaCells([
      peak('bp-1', 'candidate', 0.7, { series: SERIES }),
      peak('bp-2', 'assigned', 0.9, { mz: 200.1, series: null })
    ])
    const marks = cells.map((cell) => cell.find('.series-mark')).filter((m) => m.exists())
    expect(cells).toHaveLength(2)
    expect(marks).toHaveLength(1)
    expect(marks[0].attributes('aria-label')).toBe(
      "In the batch's CH2 series: one CH2 heavier than C5H10O6, which the batch assigns."
    )
  })
})

describe('PaneBrowserBatchPeaks isotopologue stamps', () => {
  const stamp = (parent, extra = {}) => ({
    batch_peak_id: parent,
    formula: 'C2H4O2',
    label: '81Br',
    ratio: 0.9728,
    judged: 6,
    holds: 6,
    share: 1,
    n_predicted: 4,
    n_picker: 2,
    ...extra
  })
  const isotopologues = (extra = {}) => ({
    formula: 'C2H4O2',
    n_predicted: 2,
    n_found: 1,
    n_stamped: 1,
    predicted: [
      { label: '81Br', stamped: true, judged: 6, holds: 6, share: 1, verdict: 'stamped' },
      { label: '18O', stamped: false, reason: 'not_found', verdict: 'untestable' }
    ],
    ...extra
  })
  const PARENT = peak('bp-p', 'assigned', 0.95, {
    mz: 138.94,
    consensus_formula: 'C2H4O2',
    diagnostic_isotopologues: isotopologues()
  })
  // A peak every sample assigned a formula of its own, stamped as the
  // parent's 81Br line: it folds under the parent and keeps its tier.
  const LINE = peak('bp-line', 'assigned', 0.8, {
    mz: 140.938,
    consensus_formula: 'C5H8O3',
    stamped_isotopologue_of: stamp('bp-p')
  })
  const OTHER = peak('bp-other', 'candidate', 0.5, { mz: 300.2, consensus_formula: 'C12H18O5' })
  const ids = () => wrapper.vm.rows.map((row) => row.batch_peak_id)

  it('folds a stamped line under its parent, as an isotopologue folds', async () => {
    wrapper = await mountPane({ peaks: [LINE, PARENT, OTHER] })
    expect(ids()).toEqual(['bp-p', 'bp-other'])
    expect(wrapper.vm.isotopologueCount(PARENT)).toBe(1)
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-p', 'bp-line', 'bp-other'])
    const line = wrapper.vm.rows[1]
    expect(wrapper.vm.childLabel(line)).toBe('M+2')
    expect(line.consensus_tier).toBe('assigned')
  })

  it("lets the members' own family link win over a stamp", async () => {
    const ELSEWHERE = peak('bp-m0', 'assigned', 0.9, { mz: 139.5, consensus_formula: 'C9H8' })
    const line = { ...LINE, isotopologue_of: 'bp-m0' }
    wrapper = await mountPane({ peaks: [line, PARENT, ELSEWHERE] })
    expect(wrapper.vm.isotopologueCount(ELSEWHERE)).toBe(1)
    expect(wrapper.vm.isotopologueCount(PARENT)).toBe(0)
    const row = wrapper.vm.decorated.find((r) => r.batch_peak_id === 'bp-line')
    expect(wrapper.vm.stampOf(row)).toBeNull()
  })

  it('names the isotope of a line stamped under a parent that is itself a line', async () => {
    const ROOT = peak('bp-root', 'assigned', 0.95, { mz: 136.94, consensus_formula: 'C9H8' })
    const nested = { ...PARENT, isotopologue_of: 'bp-root' }
    wrapper = await mountPane({ peaks: [ROOT, nested, LINE] })
    const row = wrapper.vm.decorated.find((r) => r.batch_peak_id === 'bp-line')
    expect(row.parentId).toBe('bp-root')
    expect(wrapper.vm.stampOf(row)).toEqual(LINE.stamped_isotopologue_of)
  })

  it('walks a family up through a stamped line to its root', async () => {
    // The 13C line of a stamped line folds under the stamped line's parent.
    const ROOT = peak('bp-root', 'assigned', 0.95, { mz: 136.94, consensus_formula: 'C9H8' })
    const middle = { ...PARENT, stamped_isotopologue_of: stamp('bp-root') }
    const C13 = peak('bp-c13', 'assigned', 0.7, { mz: 139.94, isotopologue_of: 'bp-p' })
    wrapper = await mountPane({ peaks: [ROOT, middle, C13] })
    const row = wrapper.vm.decorated.find((r) => r.batch_peak_id === 'bp-c13')
    expect(row.parentId).toBe('bp-root')
    expect(wrapper.vm.isotopologueCount(ROOT)).toBe(2)
  })

  // Renders the formula cells through a column stub that hands each its row.
  async function formulaCells(peaks) {
    const tableRows = ref([])
    app = makeApp({ peaks })
    localStorage.clear()
    setActivePinia(createPinia())
    wrapper = mount(PaneBrowserBatchPeaks, {
      global: {
        stubs: {
          ...GLOBAL_STUBS,
          DataTable: {
            ...DataTableStub,
            watch: {
              value: { handler: (value) => (tableRows.value = value), immediate: true }
            }
          },
          Column: {
            ...ColumnStub,
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="column-stub" :data-field="field">' +
              '<template v-for="(row, i) in rows" :key="i">' +
              '<div class="stub-cell" :data-row="row.batch_peak_id">' +
              '<slot name="body" :data="row" /></div></template></div>'
          }
        },
        directives: { tooltip: {}, help: {} }
      }
    })
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    const column = wrapper
      .findAll('.column-stub')
      .find((col) => col.attributes('data-field') === 'consensus_formula')
    return Object.fromEntries(
      column.findAll('.stub-cell').map((cell) => [cell.attributes('data-row'), cell])
    )
  }

  it('tags an unfolded stamped line with its isotope, dashed where the stamping predicted it', async () => {
    const PICKED = peak('bp-picked', 'assigned', 0.8, {
      mz: 140.9,
      isotopologue_of: 'bp-p',
      stamped_isotopologue_of: stamp('bp-p', { label: '37Cl', n_predicted: 0, n_picker: 6 })
    })
    const cells = await formulaCells([PARENT, LINE, PICKED, OTHER])
    const tag = cells['bp-line'].find('[data-testid="stamp-tag"]')
    expect(tag.text()).toBe('81Br')
    expect(tag.classes()).toContain('predicted')
    const picked = cells['bp-picked'].find('[data-testid="stamp-tag"]')
    expect(picked.text()).toBe('37Cl')
    expect(picked.classes()).not.toContain('predicted')
    expect(cells['bp-other'].find('[data-testid="stamp-tag"]').exists()).toBe(false)
  })

  it('marks a parent the batch shows an isotopologue of, coloured once one is stamped', async () => {
    const FOUND = peak('bp-found', 'assigned', 0.9, {
      mz: 250.1,
      diagnostic_isotopologues: isotopologues({ n_found: 1, n_stamped: 0 })
    })
    const NONE = peak('bp-none', 'assigned', 0.9, {
      mz: 260.1,
      diagnostic_isotopologues: isotopologues({ n_found: 0, n_stamped: 0 })
    })
    const cells = await formulaCells([PARENT, LINE, FOUND, NONE, OTHER])
    const mark = cells['bp-p'].find('[data-testid="isotopologues-mark"]')
    expect(mark.attributes('aria-label')).toBe('1 of 2 isotope lines stamped')
    expect(mark.classes()).toContain('stamped')
    const found = cells['bp-found'].find('[data-testid="isotopologues-mark"]')
    expect(found.exists()).toBe(true)
    expect(found.classes()).not.toContain('stamped')
    // Nothing found: the record says so on hover elsewhere, no mark here.
    expect(cells['bp-none'].find('[data-testid="isotopologues-mark"]').exists()).toBe(false)
    expect(cells['bp-other'].find('[data-testid="isotopologues-mark"]').exists()).toBe(false)
  })
})

describe('PaneBrowserBatchPeaks merged isotopologue rows', () => {
  const stripped = (parent, extra = {}) => ({
    batch_peak_id: parent,
    formula: 'C10H16O4',
    label: '13C',
    ratio: 0.1093,
    spectra: 6,
    median_factor: 1,
    above: 0,
    displaced: { formula: 'C9H12N2O2' },
    ...extra
  })
  const took = (n) => ({
    formula: 'C10H16O4',
    n_lines: n,
    lines: Array.from({ length: n }, () => ({ label: '13C', spectra: 6 }))
  })
  const PARENT = peak('bp-p', 'assigned', 0.95, {
    mz: 199.0976,
    consensus_formula: 'C10H16O4',
    merged_isotopologues: took(1)
  })
  // Its members rewritten, the row's family link names the parent.
  const LINE = peak('bp-x', 'assigned', 0.9, {
    mz: 200.101,
    consensus_formula: 'C10H16O4',
    isotopologue_of: 'bp-p',
    merged_isotopologue_of: stripped('bp-p')
  })
  const OTHER = peak('bp-other', 'candidate', 0.5, { mz: 300.2, consensus_formula: 'C12H18O5' })
  const ids = () => wrapper.vm.rows.map((row) => row.batch_peak_id)

  it('folds a stripped row under its parent and offers the filter', async () => {
    wrapper = await mountPane({ peaks: [LINE, PARENT, OTHER] })
    expect(ids()).toEqual(['bp-p', 'bp-other'])
    expect(wrapper.vm.isotopologueCount(PARENT)).toBe(1)
    expect(wrapper.vm.hasStrips).toBe(true)
  })

  it('keeps only the compounds that took rows under the filter, their rows with them', async () => {
    wrapper = await mountPane({ peaks: [LINE, PARENT, OTHER] })
    wrapper.vm.onlyStripped = true
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-p'])
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-p', 'bp-x'])
  })

  it('offers no filter, and filters nothing, where no strip stands', async () => {
    wrapper = await mountPane({
      peaks: [PARENT, OTHER].map((p) => ({ ...p, merged_isotopologues: null }))
    })
    expect(wrapper.vm.hasStrips).toBe(false)
    wrapper.vm.onlyStripped = true
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-p', 'bp-other'])
  })

  it('names the line of a stripped row only under the family it was stripped to', async () => {
    const ELSEWHERE = peak('bp-m0', 'assigned', 0.9, { mz: 199.5, consensus_formula: 'C9H8' })
    const line = { ...LINE, isotopologue_of: 'bp-m0' }
    wrapper = await mountPane({ peaks: [line, PARENT, ELSEWHERE] })
    const row = wrapper.vm.decorated.find((r) => r.batch_peak_id === 'bp-x')
    expect(row.parentId).toBe('bp-m0')
    expect(wrapper.vm.stripOf(row)).toBeNull()
  })

  async function formulaCells(peaks) {
    const tableRows = ref([])
    app = makeApp({ peaks })
    localStorage.clear()
    setActivePinia(createPinia())
    wrapper = mount(PaneBrowserBatchPeaks, {
      global: {
        stubs: {
          ...GLOBAL_STUBS,
          DataTable: {
            ...DataTableStub,
            watch: {
              value: { handler: (value) => (tableRows.value = value), immediate: true }
            }
          },
          Column: {
            ...ColumnStub,
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="column-stub" :data-field="field">' +
              '<template v-for="(row, i) in rows" :key="i">' +
              '<div class="stub-cell" :data-row="row.batch_peak_id">' +
              '<slot name="body" :data="row" /></div></template></div>'
          }
        },
        directives: { tooltip: {}, help: {} }
      }
    })
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    const column = wrapper
      .findAll('.column-stub')
      .find((col) => col.attributes('data-field') === 'consensus_formula')
    return Object.fromEntries(
      column.findAll('.stub-cell').map((cell) => [cell.attributes('data-row'), cell])
    )
  }

  it('tags an unfolded stripped row with its line, and marks the compound that took it', async () => {
    const TWO = { ...PARENT, merged_isotopologues: took(2) }
    const cells = await formulaCells([TWO, LINE, OTHER])
    const tag = cells['bp-x'].find('[data-testid="strip-tag"]')
    expect(tag.text()).toBe('13C')
    expect(tag.classes()).toContain('stripped')
    const mark = cells['bp-p'].find('[data-testid="strips-mark"]')
    expect(mark.attributes('aria-label')).toBe("2 rows stripped to this compound's isotope lines")
    expect(cells['bp-other'].find('[data-testid="strips-mark"]').exists()).toBe(false)
    expect(cells['bp-other'].find('[data-testid="strip-tag"]').exists()).toBe(false)
  })

  // The known-species decision's mark sits beside the strip's in the same cell.
  it('marks a known species with the batch verdict, present ones in the primary colour', async () => {
    const decided = (verdict) => ({
      species: { kind: 'library', label: '[(CH4N2O)3+H]+', ion_formula: 'C3H13N6O3+' },
      verdict,
      files: 6,
      present: verdict === 'unpooled' ? 1 : 5,
      test_held: 1
    })
    const cells = await formulaCells([
      { ...PARENT, known_species: decided('present') },
      { ...OTHER, known_species: decided('unpooled') },
      LINE
    ])
    const present = cells['bp-p'].find('[data-testid="known-species-mark"]')
    expect(present.attributes('aria-label')).toBe('[(CH4N2O)3+H]+: present across the batch')
    expect(present.classes()).toContain('present')
    const unpooled = cells['bp-other'].find('[data-testid="known-species-mark"]')
    expect(unpooled.classes()).toContain('unpooled')
    expect(unpooled.classes()).not.toContain('present')
    expect(cells['bp-x'].find('[data-testid="known-species-mark"]').exists()).toBe(false)
  })
})

describe('PaneBrowserBatchPeaks co-variation column', () => {
  const reading = (verdict, r, extra = {}) => ({
    formula: 'C6H12O6',
    ionization_mechanism_id: 'm-1',
    channel: '[M-H]-',
    verdict,
    r,
    n_spectra: 412,
    reason: null,
    partner_batch_peak_id: 'bp-other',
    partner_channel: '[M+NO3]-',
    partners: [
      { batch_peak_id: 'bp-other', channel: '[M+NO3]-', verdict, r, n_spectra: 412, reason: null }
    ],
    normalised: true,
    ...extra
  })
  const peaks = () => [
    peak('bp-1', 'assigned', 0.9, { mz: 100.1, co_variation: reading('co_varying', 0.82) }),
    peak('bp-2', 'candidate', 0.6, { mz: 200.1, co_variation: reading('not_co_varying', -0.4) }),
    peak('bp-3', 'assigned', 0.8, {
      mz: 300.1,
      co_variation: reading('untestable', null, { reason: 'flat' })
    }),
    peak('bp-4', 'assigned', 0.95, { mz: 400.1, co_variation: null }),
    peak('bp-5', 'assigned', 0.7, { mz: 401.1, isotopologue_of: 'bp-4' })
  ]

  // Renders a column's cells through a stub that hands each its row.
  async function cellsOf(field, rows) {
    const tableRows = ref([])
    app = makeApp({ peaks: rows })
    localStorage.clear()
    setActivePinia(createPinia())
    wrapper = mount(PaneBrowserBatchPeaks, {
      global: {
        stubs: {
          ...GLOBAL_STUBS,
          DataTable: {
            ...DataTableStub,
            watch: {
              value: { handler: (value) => (tableRows.value = value), immediate: true }
            }
          },
          Column: {
            ...ColumnStub,
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="column-stub" :data-field="field">' +
              '<template v-for="(row, i) in rows" :key="i">' +
              '<div class="stub-cell" :data-row="row.batch_peak_id">' +
              '<slot name="body" :data="row" /></div></template></div>'
          }
        },
        directives: { tooltip: {}, help: {} }
      }
    })
    await wrapper.vm.$nextTick()
    const column = wrapper
      .findAll('.column-stub')
      .find((col) => col.attributes('data-field') === field)
    if (!column) return null
    return Object.fromEntries(
      column.findAll('.stub-cell').map((cell) => [cell.attributes('data-row'), cell])
    )
  }

  it('is absent from a ledger no reading reached', async () => {
    wrapper = await mountPane({ peaks: peaks().map(({ co_variation, ...bp }) => bp) })
    expect(columnFor('coVariationRank')).toBeUndefined()
  })

  it("shows the strongest partner's r, or that the pair cannot be read", async () => {
    const cells = await cellsOf('coVariationRank', peaks())
    expect(cells['bp-1'].find('[data-testid="co-variation"]').text()).toBe('r 0.82')
    expect(cells['bp-1'].find('.co-variation').classes()).toContain('co_varying')
    expect(cells['bp-2'].find('.co-variation').text()).toBe('r -0.40')
    expect(cells['bp-2'].find('.co-variation').classes()).toContain('not_co_varying')
    expect(cells['bp-3'].find('.co-variation').text()).toBe('untestable')
    // No reading: a dash.
    expect(cells['bp-4'].find('.co-variation').exists()).toBe(false)
    expect(cells['bp-4'].find('.no-co-variation').exists()).toBe(true)
  })

  it('leaves an unfolded isotopologue row blank', async () => {
    await cellsOf('coVariationRank', peaks())
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    const column = wrapper
      .findAll('.column-stub')
      .find((col) => col.attributes('data-field') === 'coVariationRank')
    const child = column.findAll('.stub-cell').find((c) => c.attributes('data-row') === 'bp-5')
    expect(child.exists()).toBe(true)
    expect(child.text()).toBe('')
  })

  it('sorts on r, an unread pair last in both directions', async () => {
    wrapper = await mountPane({ peaks: peaks() })
    expect(columnFor('coVariationRank').props('sortable')).toBe(true)
    const byId = Object.fromEntries(wrapper.vm.rows.map((row) => [row.batch_peak_id, row]))
    expect(byId['bp-1'].coVariationRank).toBe(0.82)
    expect(byId['bp-3'].coVariationRank).toBeNull()
    wrapper.vm.sortField = 'coVariationRank'
    wrapper.vm.sortOrder = -1
    await wrapper.vm.$nextTick()
    const top = () => wrapper.vm.rows.filter((row) => !row.parentId).map((row) => row.batch_peak_id)
    expect(top().slice(0, 2)).toEqual(['bp-1', 'bp-2'])
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()
    expect(top().slice(0, 2)).toEqual(['bp-2', 'bp-1'])
  })

  it('explains the column, and that no tier moves on it', async () => {
    wrapper = await mountPane({ peaks: peaks() })
    expect(wrapper.vm.HEADER_TOOLTIPS.coVariation).toMatch(/0\.6 or more co-varies/)
    expect(wrapper.vm.HEADER_TOOLTIPS.coVariation).toMatch(/No tier moves on it$/)
  })
})

describe('PaneBrowserBatchPeaks facts column', () => {
  const checks = (facts) => ({
    formula: 'C2H4O2',
    ion_formula: 'C2H4BrO2-',
    ionization_mechanism_id: 'm-1',
    ...facts
  })
  const lock = (verdict, extra = {}) => ({
    halogen_lock: {
      element: 'Br',
      label: '81Br',
      n_ion: 1,
      n_neutral: 0,
      judged: 6,
      holds: 6,
      share: 1,
      verdict,
      ...extra
    }
  })
  const twin = (verdict) => ({ label_twin: { judged: 6, present: 0, excess: null, verdict } })
  const peaks = () => [
    peak('bp-1', 'assigned', 0.9, { mz: 100.1, batch_checks: checks(lock('locked')) }),
    peak('bp-2', 'candidate', 0.6, {
      mz: 200.1,
      batch_checks: checks({ ...lock('untestable', { reason: 'few_spectra' }), ...twin('absent') })
    }),
    peak('bp-3', 'assigned', 0.95, { mz: 300.1, batch_checks: null }),
    peak('bp-4', 'assigned', 0.7, { mz: 301.1, isotopologue_of: 'bp-3' })
  ]

  // Renders the column's cells through a stub that hands each its row.
  async function cells(rows) {
    const tableRows = ref([])
    app = makeApp({ peaks: rows })
    localStorage.clear()
    setActivePinia(createPinia())
    wrapper = mount(PaneBrowserBatchPeaks, {
      global: {
        stubs: {
          ...GLOBAL_STUBS,
          DataTable: {
            ...DataTableStub,
            watch: { value: { handler: (value) => (tableRows.value = value), immediate: true } }
          },
          Column: {
            ...ColumnStub,
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="column-stub" :data-field="field">' +
              '<template v-for="(row, i) in rows" :key="i">' +
              '<div class="stub-cell" :data-row="row.batch_peak_id">' +
              '<slot name="body" :data="row" /></div></template></div>'
          }
        },
        directives: { tooltip: {}, help: {} }
      }
    })
    await wrapper.vm.$nextTick()
    const column = wrapper
      .findAll('.column-stub')
      .find((col) => col.attributes('data-field') === 'checksRank')
    return Object.fromEntries(
      column.findAll('.stub-cell').map((cell) => [cell.attributes('data-row'), cell])
    )
  }

  it('is absent from a ledger no reading reached', async () => {
    wrapper = await mountPane({ peaks: peaks().map(({ batch_checks, ...bp }) => bp) })
    expect(columnFor('checksRank')).toBeUndefined()
  })

  it('shows a tag per fact, each in its tone', async () => {
    const found = await cells(peaks())
    const tags = (id) =>
      found[id].findAll('[data-testid="check-tag"]').map((tag) => [tag.text(), tag.classes()])
    expect(tags('bp-1')).toEqual([['Br lock', ['check-tag', 'holds']]])
    expect(tags('bp-2')).toEqual([
      ['Br lock?', ['check-tag', 'unread']],
      ['no 14N twin', ['check-tag', 'refutes']]
    ])
    // None read: a dash, and an unfolded isotopologue row stays blank.
    expect(found['bp-3'].find('[data-testid="batch-checks"]').exists()).toBe(false)
    expect(found['bp-3'].find('.no-co-variation').exists()).toBe(true)
  })

  it('sorts the facts that hold first, a row with none last', async () => {
    wrapper = await mountPane({ peaks: peaks() })
    expect(columnFor('checksRank').props('sortable')).toBe(true)
    const byId = Object.fromEntries(wrapper.vm.rows.map((row) => [row.batch_peak_id, row]))
    expect(byId['bp-1'].checksRank).toBe(1)
    expect(byId['bp-2'].checksRank).toBe(-1)
    expect(byId['bp-3'].checksRank).toBeNull()
    wrapper.vm.sortField = 'checksRank'
    wrapper.vm.sortOrder = -1
    await wrapper.vm.$nextTick()
    const top = () => wrapper.vm.rows.filter((row) => !row.parentId).map((row) => row.batch_peak_id)
    expect(top()).toEqual(['bp-1', 'bp-2', 'bp-3'])
  })

  it('explains the column, and that no tier moves on it', async () => {
    wrapper = await mountPane({ peaks: peaks() })
    expect(wrapper.vm.HEADER_TOOLTIPS.facts).toMatch(/14N twin/)
    expect(wrapper.vm.HEADER_TOOLTIPS.facts).toMatch(/No tier moves on them$/)
  })
})

describe('PaneBrowserBatchPeaks selection cap', () => {
  const many = (n) => Array.from({ length: n }, (_, i) => peak(`bp-${i}`, 'assigned', 0.9))

  const table = () => wrapper.findComponent(DataTableStub)
  const notice = () =>
    wrapper.findAll('.pane-message').find((message) => /matching rows/.test(message.text()))

  /** Select all filtered rows the way the header checkbox does. */
  const checkSelectAll = (checked = true) =>
    table().vm.$emit('select-all-change', { checked, originalEvent: {} })

  it('caps what the header checkbox selects, and says so', async () => {
    wrapper = await mountPane({ peaks: many(MAX_SELECTED_BATCH_PEAKS + 50) })

    checkSelectAll()
    await wrapper.vm.$nextTick()

    expect(app.data.batchPeak.selected).toHaveLength(MAX_SELECTED_BATCH_PEAKS)
    expect(notice().text()).toMatch(
      new RegExp(`${MAX_SELECTED_BATCH_PEAKS} of the ${MAX_SELECTED_BATCH_PEAKS + 50} matching`)
    )
  })

  it('caps Ctrl+A, which selects the filtered rows without going through the table', async () => {
    wrapper = await mountPane({ peaks: many(MAX_SELECTED_BATCH_PEAKS + 50) })

    await wrapper.find('.datatable').trigger('keydown', { ctrlKey: true, key: 'a' })

    expect(app.data.batchPeak.selected).toHaveLength(MAX_SELECTED_BATCH_PEAKS)
    expect(notice().exists()).toBe(true)
  })

  it('caps a range selection, which the table writes without asking for all', async () => {
    // Shift-clicking a range emits the rows directly rather than going through
    // the select-all handler, so a cap on select-all alone would not see it.
    wrapper = await mountPane({ peaks: many(MAX_SELECTED_BATCH_PEAKS + 50) })

    table().vm.$emit('update:selection', wrapper.vm.rows)
    await wrapper.vm.$nextTick()

    expect(app.data.batchPeak.selected).toHaveLength(MAX_SELECTED_BATCH_PEAKS)
  })

  it('keeps the rows the ledger is showing first, so a filter chooses which', async () => {
    wrapper = await mountPane({ peaks: many(MAX_SELECTED_BATCH_PEAKS + 50) })

    checkSelectAll()
    await wrapper.vm.$nextTick()

    const selected = app.data.batchPeak.selected.map((row) => row.batch_peak_id)
    const shownFirst = wrapper.vm.rows
      .slice(0, MAX_SELECTED_BATCH_PEAKS)
      .map((row) => row.batch_peak_id)
    expect(selected).toEqual(shownFirst)
  })

  it('says nothing when the selection fits', async () => {
    wrapper = await mountPane({ peaks: many(12) })

    checkSelectAll()
    await wrapper.vm.$nextTick()

    expect(app.data.batchPeak.selected).toHaveLength(12)
    expect(notice()).toBeUndefined()
  })

  it('reads the header checkbox as checked at the cap, so it can still clear', async () => {
    // Left to itself the table compares the selection against every filtered
    // row, never sees them all selected at the cap, and so offers only to
    // re-select the same rows - with no gesture left that empties the selection.
    wrapper = await mountPane({ peaks: many(MAX_SELECTED_BATCH_PEAKS + 50) })
    expect(table().props('selectAll')).toBe(false)

    checkSelectAll()
    await wrapper.vm.$nextTick()
    expect(table().props('selectAll')).toBe(true)

    checkSelectAll(false)
    await wrapper.vm.$nextTick()
    expect(app.data.batchPeak.selected).toHaveLength(0)
    expect(table().props('selectAll')).toBe(false)
    expect(notice()).toBeUndefined()
  })

  it('does not read as all-selected over rows none of which are selected', async () => {
    // Narrowing the filter after a large selection leaves the selection as big
    // as the rows now on screen while sharing none of them. A checkbox that
    // compared sizes would tick over rows that are all unselected - and, since
    // a ticked box offers to clear, the next click would wipe the selection
    // instead of filling it.
    //
    // Narrowed through the tier chips, the control a user actually has: the
    // table is `lazy`, so the rows on screen are the ones the pane filtered.
    wrapper = await mountPane({
      peaks: [...many(3), peak('other-1', 'candidate', 0.5), peak('other-2', 'candidate', 0.4)]
    })
    const chip = (tier) =>
      wrapper.findAll('.tier-stat')[tier === 'assigned' ? 0 : 1].trigger('click')

    // Filter to the three assigned rows and select all of them.
    await chip('assigned')
    expect(wrapper.vm.rows).toHaveLength(3)
    checkSelectAll()
    await wrapper.vm.$nextTick()
    expect(table().props('selectAll')).toBe(true)

    // Now filter to the two candidate rows: the selection is still three rows,
    // larger than what is on screen, and shares none of it.
    await chip('assigned') // clear
    await chip('candidate')

    expect(app.data.batchPeak.selected).toHaveLength(3)
    expect(wrapper.vm.rows).toHaveLength(2)
    expect(table().props('selectAll')).toBe(false)
  })

  it('refuses one row at the cap without claiming a filter matched more', async () => {
    // A row click hands over the selection with the clicked row appended, so at
    // the cap it arrives one over and the slice drops the row that was clicked.
    // Saying "300 of the 301 matching rows" would name a number that is not the
    // size of anything the user can see.
    wrapper = await mountPane({ peaks: many(MAX_SELECTED_BATCH_PEAKS + 50) })
    checkSelectAll()
    await wrapper.vm.$nextTick()

    const held = app.data.batchPeak.selected
    const oneMore = wrapper.vm.rows[MAX_SELECTED_BATCH_PEAKS]
    table().vm.$emit('update:selection', [...held, oneMore])
    await wrapper.vm.$nextTick()

    expect(app.data.batchPeak.selected).toHaveLength(MAX_SELECTED_BATCH_PEAKS)
    expect(notice()).toBeUndefined()
    const full = wrapper
      .findAll('.pane-message')
      .find((message) => /selection is full/.test(message.text()))
    expect(full.text()).toMatch(new RegExp(`full at ${MAX_SELECTED_BATCH_PEAKS} batch peaks`))
    expect(full.text()).not.toMatch(String(MAX_SELECTED_BATCH_PEAKS + 1))
  })

  it('survives the bare row the table sends when a range collapses onto one', async () => {
    // Shift+Space emits `rowData` rather than an array of one when the range it
    // would select is the row already focused.
    wrapper = await mountPane({ peaks: many(4) })

    table().vm.$emit('update:selection', wrapper.vm.rows[2])
    await wrapper.vm.$nextTick()

    expect(app.data.batchPeak.selected.map((row) => row.batch_peak_id)).toEqual(['bp-2'])
  })

  it('drops the notice when the batch changes', async () => {
    wrapper = await mountPane({ peaks: many(MAX_SELECTED_BATCH_PEAKS + 50) })
    checkSelectAll()
    await wrapper.vm.$nextTick()
    expect(notice().exists()).toBe(true)

    app.data.batch.focusedId = 'b-2'
    await wrapper.vm.$nextTick()

    expect(notice()).toBeUndefined()
  })
})

// The batch ledger has no family link of its own to read - a batch peak is an
// m/z anchor - so `isotopologue_of` is derived by the backend and arrives one hop
// deep, with the parent it names not guaranteed to be in the list. Everything
// below is what the pane has to make of that: fold the families, keep them
// together under any sort, and never lose a row whose link it could not follow.
describe('PaneBrowserBatchPeaks isotopologue folding', () => {
  const M0 = peak('bp-m0', 'assigned', 0.95, { mz: 181.0707, consensus_formula: 'C6H12O6' })
  const ISO1 = peak('bp-iso-1', 'assigned', 0.9, {
    mz: 182.0741,
    consensus_formula: 'C6H12O6',
    isotopologue_of: 'bp-m0'
  })
  const ISO2 = peak('bp-iso-2', 'assigned', 0.85, {
    mz: 183.0775,
    consensus_formula: 'C6H12O6',
    isotopologue_of: 'bp-m0'
  })
  const OTHER = peak('bp-other', 'candidate', 0.5, { mz: 300.2, consensus_formula: 'C12H18O5' })

  const family = [M0, ISO2, ISO1, OTHER]
  const ids = () => wrapper.vm.rows.map((row) => row.batch_peak_id)

  it('folds isotopologues away by default, leaving one row per species', async () => {
    wrapper = await mountPane({ peaks: family })

    expect(ids()).toEqual(['bp-m0', 'bp-other'])
  })

  it('counts the folded isotopologues on the row they folded into', async () => {
    wrapper = await mountPane({ peaks: family })

    expect(wrapper.vm.isotopologueCount(M0)).toBe(2)
    expect(wrapper.vm.isotopologueCount(OTHER)).toBe(0)
  })

  it('unfolds them directly under their main peak, in m/z order', async () => {
    // Seeded out of order above: a family reads M0 first and then M+1, M+2,
    // which is the m/z order, not the order the ledger happened to arrive in.
    wrapper = await mountPane({ peaks: family })

    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()

    expect(ids()).toEqual(['bp-m0', 'bp-iso-1', 'bp-iso-2', 'bp-other'])
  })

  it('labels an unfolded isotopologue by its offset from the peak it folds under', async () => {
    wrapper = await mountPane({ peaks: family })
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()

    const [, first, second] = wrapper.vm.rows
    expect(wrapper.vm.childLabel(first)).toBe('M+1')
    expect(wrapper.vm.childLabel(second)).toBe('M+2')
  })

  it('keeps a family together under a sort that would tear it apart', async () => {
    // PrimeVue sorts the flat array it is handed, which is why the sort is the
    // pane's: sorting by m/z descending puts the heaviest isotopologue first and
    // drops its M0 to the bottom, where an indented "M+2" means nothing.
    wrapper = await mountPane({ peaks: family })
    wrapper.vm.showIsotopologues = true
    wrapper.vm.sortField = 'mz'
    wrapper.vm.sortOrder = -1
    await wrapper.vm.$nextTick()

    expect(ids()).toEqual(['bp-other', 'bp-m0', 'bp-iso-1', 'bp-iso-2'])
  })

  it('keeps every family contiguous under every sortable column', async () => {
    // The one-column case above is the regression that prompted the pane taking
    // the sort over; this is the invariant, so a column added later cannot
    // quietly reintroduce it.
    wrapper = await mountPane({ peaks: family })
    wrapper.vm.showIsotopologues = true

    for (const field of ['mz', 'max_intensity', 'consensus_formula', 'tierRank', 'n_present']) {
      for (const order of [1, -1]) {
        wrapper.vm.sortField = field
        wrapper.vm.sortOrder = order
        await wrapper.vm.$nextTick()

        // Every isotopologue sits directly under its own parent, so a family is
        // one unbroken block wherever the sort put it. Collected rather than
        // asserted row by row, so a failure names the column and the direction.
        let parent = null
        const misplaced = []
        for (const row of wrapper.vm.rows) {
          if (!row.parentId) parent = row.batch_peak_id
          else if (row.parentId !== parent) misplaced.push(row.batch_peak_id)
        }
        expect({ field, order, misplaced }).toEqual({ field, order, misplaced: [] })
        expect(wrapper.vm.rows).toHaveLength(4)
      }
    }
  })

  it('shows an isotopologue whose parent is not in the ledger as a row of its own', async () => {
    // The link is one hop into a list this pane does not control: the anchor it
    // names can have been filtered out by the request or deleted since. Folding
    // the row under a parent that is never drawn would remove it from the
    // ledger altogether.
    wrapper = await mountPane({
      peaks: [OTHER, peak('bp-orphan', 'assigned', 0.8, { isotopologue_of: 'bp-gone' })]
    })

    expect(ids()).toContain('bp-orphan')
    expect(wrapper.vm.rows.find((row) => row.batch_peak_id === 'bp-orphan').parentId).toBeNull()
  })

  it('folds a chain onto its root, so the table stays two levels deep', async () => {
    // The backend links what its members observed, one hop, and a chain is
    // possible when one anchor is an isotopologue in most samples and an M0 in the
    // rest. A row nested under a row that is itself nested would break the fixed
    // row height the virtual scroller needs.
    wrapper = await mountPane({
      peaks: [
        M0,
        ISO1,
        peak('bp-deep', 'assigned', 0.7, { mz: 184.08, isotopologue_of: 'bp-iso-1' })
      ]
    })

    expect(ids()).toEqual(['bp-m0'])
    expect(wrapper.vm.isotopologueCount(M0)).toBe(2)
  })

  it('draws both rows of a cycle rather than losing them', async () => {
    // Nothing should produce one, and a ledger that walked into one without a
    // guard would hang rather than render.
    wrapper = await mountPane({
      peaks: [
        peak('bp-a', 'assigned', 0.9, { isotopologue_of: 'bp-b' }),
        peak('bp-b', 'assigned', 0.8, { isotopologue_of: 'bp-a' })
      ]
    })

    expect(ids().sort()).toEqual(['bp-a', 'bp-b'])
  })
})

// `lazy` hands sorting AND filtering back to the pane. The chips and the column
// menus keep looking active either way, so what these hold is that they still
// change the rows - the regression the switch to a pane-owned sort could make
// invisible.
describe('PaneBrowserBatchPeaks filtering under the pane-owned table', () => {
  const peaks = [
    peak('bp-1', 'assigned', 0.9, { consensus_formula: 'C6H12O6' }),
    peak('bp-2', 'candidate', 0.5, { consensus_formula: 'C12H18O5' }),
    peak('bp-3', 'assigned', 0.8, { consensus_formula: 'C6H14O6' })
  ]
  const ids = () => wrapper.vm.rows.map((row) => row.batch_peak_id)

  it('narrows the rows when a tier chip is clicked, not just the constraint', async () => {
    wrapper = await mountPane({ peaks })

    await wrapper.findAll('.tier-stat')[1].trigger('click')

    expect(ids()).toEqual(['bp-2'])
  })

  it('narrows the rows on the formula filter', async () => {
    wrapper = await mountPane({ peaks })

    wrapper.vm.filters.consensus_formula.constraints[0].value = 'C6H'
    await wrapper.vm.$nextTick()

    expect(ids()).toEqual(['bp-1', 'bp-3'])
  })

  it('honours the match mode the menu offers rather than assuming one', async () => {
    // The menu lets the user pick "Not contains" and the rest; a filter that
    // hard-coded CONTAINS would go on matching while the menu said otherwise.
    wrapper = await mountPane({ peaks })

    wrapper.vm.filters.consensus_formula.constraints[0].matchMode = 'notContains'
    wrapper.vm.filters.consensus_formula.constraints[0].value = 'C6H'
    await wrapper.vm.$nextTick()

    expect(ids()).toEqual(['bp-2'])
  })

  it('shows every row again when the constraint is cleared', async () => {
    wrapper = await mountPane({ peaks })
    wrapper.vm.filters.consensus_formula.constraints[0].value = 'C6H'
    await wrapper.vm.$nextTick()

    wrapper.vm.filters.consensus_formula.constraints[0].value = null
    await wrapper.vm.$nextTick()

    expect(ids()).toHaveLength(3)
  })

  it('keeps a family whole when its main peak passes the filter', async () => {
    // An isotopologue shares its family's formula and tier, so a filter that kept
    // the parent kept the family - and filtering the isotopologues separately would
    // only ever produce the same answer or an incomplete family.
    wrapper = await mountPane({
      peaks: [
        peak('bp-m0', 'assigned', 0.9, { mz: 181.07, consensus_formula: 'C6H12O6' }),
        peak('bp-iso', 'assigned', 0.9, {
          mz: 182.07,
          consensus_formula: 'C6H12O6',
          isotopologue_of: 'bp-m0'
        }),
        peak('bp-other', 'candidate', 0.5, { consensus_formula: 'C12H18O5' })
      ]
    })
    wrapper.vm.showIsotopologues = true
    wrapper.vm.filters.consensus_formula.constraints[0].value = 'C6H12O6'
    await wrapper.vm.$nextTick()

    expect(ids()).toEqual(['bp-m0', 'bp-iso'])
  })
})

describe('PaneBrowserBatchPeaks intensity column', () => {
  const peaks = [
    peak('bp-mid', 'assigned', 0.9, {
      max_intensity: 5000,
      intensity_variable: 'sum_peak_heights'
    }),
    peak('bp-high', 'assigned', 0.9, { max_intensity: 90000 }),
    peak('bp-none', 'assigned', 0.9, { max_intensity: null }),
    peak('bp-low', 'assigned', 0.9, { max_intensity: 12 })
  ]
  const ids = () => wrapper.vm.rows.map((row) => row.batch_peak_id)

  const sortBy = async (field, order) => {
    wrapper.vm.sortField = field
    wrapper.vm.sortOrder = order
    await wrapper.vm.$nextTick()
  }

  it('offers a sortable intensity column', async () => {
    wrapper = await mountPane({ peaks })

    expect(columnFor('max_intensity').props('sortable')).toBe(true)
  })

  it('sorts by intensity, brightest first', async () => {
    wrapper = await mountPane({ peaks })
    await sortBy('max_intensity', -1)

    expect(ids().slice(0, 3)).toEqual(['bp-high', 'bp-mid', 'bp-low'])
  })

  it('sorts a peak with no intensity last in both directions', async () => {
    // No intensity is unknown, not zero: a row that sorted first ascending would
    // put the peaks with nothing to report at the top of "quietest first".
    wrapper = await mountPane({ peaks })

    await sortBy('max_intensity', -1)
    expect(ids().at(-1)).toBe('bp-none')

    await sortBy('max_intensity', 1)
    expect(ids().at(-1)).toBe('bp-none')
  })

  it('names the aggregate and the unit it is in', async () => {
    // One number stands for a whole per-sample matrix, so the column has to say
    // which one - and in what, since the unit is the instrument's.
    wrapper = await mountPane({ peaks })

    expect(wrapper.vm.intensityTooltip).toMatch(/highest intensity/i)
    expect(wrapper.vm.intensityTooltip).toMatch(/any sample/i)
    expect(wrapper.vm.intensityTooltip).toMatch(/summed peak height/i)
  })

  it('names no unit when the ledger carries none', async () => {
    wrapper = await mountPane({ peaks: [peak('bp-1', 'assigned', 0.9)] })

    expect(wrapper.vm.intensityTooltip).not.toMatch(/\(/)
  })
})

describe('PaneBrowserBatchPeaks selection across the fold', () => {
  const M0 = peak('bp-m0', 'assigned', 0.9, { mz: 181.07 })
  const ISO = peak('bp-iso', 'assigned', 0.9, { mz: 182.07, isotopologue_of: 'bp-m0' })
  const OTHER = peak('bp-other', 'candidate', 0.5, { mz: 300.2 })

  const table = () => wrapper.findComponent(DataTableStub)
  const selectedIds = () => app.data.batchPeak.selected.map((row) => row.batch_peak_id)

  it('drops the isotopologues it hides from the selection', async () => {
    // The selection is what the chart plots. Leaving a hidden row in it draws a
    // trace with no ticked row behind it, and spends the cap on a row the user
    // can no longer see to release.
    wrapper = await mountPane({ peaks: [M0, ISO, OTHER] })
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()

    table().vm.$emit('update:selection', wrapper.vm.rows)
    await wrapper.vm.$nextTick()
    expect(selectedIds()).toEqual(['bp-m0', 'bp-iso', 'bp-other'])

    wrapper.vm.showIsotopologues = false
    await wrapper.vm.$nextTick()

    expect(selectedIds()).toEqual(['bp-m0', 'bp-other'])
  })

  it('leaves the selection alone when a tier chip hides a row', async () => {
    // A filter is not a fold: the rows it hides are still rows, and narrowing
    // the ledger to choose the next few hundred has never cost the selection.
    wrapper = await mountPane({ peaks: [M0, ISO, OTHER] })
    table().vm.$emit('update:selection', wrapper.vm.rows)
    await wrapper.vm.$nextTick()

    await wrapper.findAll('.tier-stat')[1].trigger('click')

    expect(selectedIds()).toEqual(['bp-m0', 'bp-other'])
  })

  it('does not give the isotopologues back selected when they are unfolded again', async () => {
    wrapper = await mountPane({ peaks: [M0, ISO] })
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    table().vm.$emit('update:selection', wrapper.vm.rows)
    await wrapper.vm.$nextTick()

    wrapper.vm.showIsotopologues = false
    await wrapper.vm.$nextTick()
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()

    expect(selectedIds()).toEqual(['bp-m0'])
  })

  it('is toggled from the view menu on the tier row', async () => {
    // Where the sample ledger keeps it: behind the cog at the end of the filter
    // row, rather than on the panel header it used to share with the compute
    // button. The toggle is inside the popover the cog opens.
    wrapper = await mountPane({ peaks: [M0, ISO] })

    const cog = wrapper.find('.tier-strip .view-menu-button')
    expect(cog.exists()).toBe(true)
    expect(wrapper.find('.view-popover .iso-toggle').exists()).toBe(true)

    await wrapper.find('.iso-toggle').trigger('click')

    expect(wrapper.vm.showIsotopologues).toBe(true)
    expect(wrapper.vm.rows).toHaveLength(2)
  })
})

describe('PaneBrowserBatchPeaks batch-level verdicts', () => {
  /** A live batch-level verdict on `batch_peak_id`, about `assigned_formula`. */
  const verdictOn = (batch_peak_id, verdict, assigned_formula = 'C6H12O6') => ({
    batch_peak_verification_id: `v-${batch_peak_id}`,
    batch_peak_id,
    assigned_formula,
    ionization_mechanism_id: null,
    verdict,
    superseded_utc: null
  })
  const PEAKS = [
    peak('bp-1', 'assigned', 0.9),
    peak('bp-2', 'assigned', 0.8),
    peak('bp-3', 'assigned', 0.7),
    peak('bp-4', 'assigned', 0.6)
  ]
  const VERDICTS = {
    'bp-1': verdictOn('bp-1', 'unsure'),
    'bp-2': verdictOn('bp-2', 'confirmed'),
    'bp-4': verdictOn('bp-4', 'rejected')
  }

  it('carries a verdict rank on every row, confirmed first and unjudged last', async () => {
    wrapper = await mountPane({ peaks: PEAKS, verdicts: VERDICTS })

    const rank = new Map(wrapper.vm.rows.map((row) => [row.batch_peak_id, row.verdictRank]))
    expect(rank.get('bp-2')).toBe(0)
    expect(rank.get('bp-4')).toBe(1)
    expect(rank.get('bp-1')).toBe(2)
    expect(rank.get('bp-3')).toBeNull()
  })

  it('offers a sortable verdict column after Samples, sorting on that rank', async () => {
    wrapper = await mountPane({ peaks: PEAKS, verdicts: VERDICTS })

    const columns = wrapper.findAllComponents(ColumnStub).map((column) => column.props('field'))
    expect(columns.at(-1)).toBe('verdictRank')
    expect(columns.at(-2)).toBe('n_present')
    expect(columnFor('verdictRank').props('sortable')).toBe(true)
  })

  it('sorts the column through the pane, unjudged rows last either way', async () => {
    wrapper = await mountPane({ peaks: PEAKS, verdicts: VERDICTS })

    wrapper.vm.sortField = 'verdictRank'
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.rows.map((row) => row.batch_peak_id)).toEqual([
      'bp-2',
      'bp-4',
      'bp-1',
      'bp-3'
    ])

    wrapper.vm.sortOrder = -1
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.rows.map((row) => row.batch_peak_id)).toEqual([
      'bp-1',
      'bp-4',
      'bp-2',
      'bp-3'
    ])
  })

  it('reads a verdict about a formula the consensus has left as stale, and says so', async () => {
    wrapper = await mountPane({
      peaks: [peak('bp-1', 'assigned', 0.9), peak('bp-2', 'assigned', 0.8)],
      verdicts: {
        'bp-1': verdictOn('bp-1', 'confirmed', 'C7H14O7'),
        'bp-2': verdictOn('bp-2', 'confirmed')
      }
    })
    const byId = new Map(wrapper.vm.rows.map((row) => [row.batch_peak_id, row]))

    expect(wrapper.vm.verdictStale(byId.get('bp-1'))).toBe(true)
    expect(wrapper.vm.verdictTooltip(byId.get('bp-1'))).toBe(
      'Confirmed as C7H14O7 - the consensus is now C6H12O6. Re-judge or retract.'
    )
    // A current verdict speaks through its badge; an unjudged row invites one.
    expect(wrapper.vm.verdictStale(byId.get('bp-2'))).toBe(false)
    expect(wrapper.vm.verdictTooltip(byId.get('bp-2'))).toBe('')
    expect(wrapper.vm.verdictTooltip({ batch_peak_id: 'bp-9', consensus_formula: null })).toBe(
      'Record a batch-level verdict'
    )
  })

  it('points the verdict popover at the live row it was opened from', async () => {
    wrapper = await mountPane({ peaks: PEAKS, verdicts: VERDICTS })

    expect(wrapper.vm.verdictTarget).toBeNull()
    wrapper.vm.verdictRowId = 'bp-2'
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.verdictTarget.batch_peak_id).toBe('bp-2')
    // The row as the ledger now holds it, not a copy taken when the cell was clicked.
    expect(wrapper.vm.verdictTarget.verdictRank).toBe(0)
  })
})

describe('PaneBrowserBatchPeaks batch runs', () => {
  /** Put an earlier run on screen, as the run selector does. */
  async function viewEarlierRun(action = 'search_untargeted') {
    app.data.batchPeakRun.viewing = { batch_peak_run_id: 'r-old', action, current: false }
    app.data.batchPeakRun.viewingCurrent = false
    app.data.batchPeakRun.viewingId = 'r-old'
    await wrapper.vm.$nextTick()
  }

  it('says nothing about history while the live ledger is on screen', async () => {
    wrapper = await mountPane({ peaks: [peak('bp-1', 'assigned', 0.9)] })
    expect(wrapper.findAll('.pane-message')).toHaveLength(0)
  })

  it('names the run whose snapshot is on screen, and that it is read-only', async () => {
    wrapper = await mountPane({ peaks: [peak('bp-1', 'assigned', 0.9)] })
    await viewEarlierRun('search_untargeted')

    const notice = wrapper.findAll('.pane-message').map((message) => message.text())
    expect(notice).toHaveLength(1)
    expect(notice[0]).toContain('as an untargeted search left it, read-only')
    expect(notice[0]).toContain('pick it in the run selector')
  })

  it('describes each kind of run in words rather than by its code', async () => {
    wrapper = await mountPane({ peaks: [peak('bp-1', 'assigned', 0.9)] })
    for (const [action, label] of [
      ['fold', 'folded samples'],
      ['rebuild', 'a rebuild'],
      ['import', 'an import'],
      ['detect_series', 'a series detection'],
      ['read_co_variation', 'a co-variation reading'],
      ['stamp_isotopologues', 'an isotopologue stamping'],
      ['strip_isotopologues', 'a strip of isotopologue rows'],
      ['read_batch_checks', 'a batch checks reading'],
      ['unknown_action', 'unknown_action']
    ]) {
      await viewEarlierRun(action)
      expect(wrapper.vm.viewedRunLabel).toBe(label)
    }
  })

  it('drops the notice when the current run is picked again', async () => {
    wrapper = await mountPane({ peaks: [peak('bp-1', 'assigned', 0.9)] })
    await viewEarlierRun()
    expect(wrapper.findAll('.pane-message')).toHaveLength(1)

    app.data.batchPeakRun.viewing = { batch_peak_run_id: 'r-new', action: 'rebuild', current: true }
    app.data.batchPeakRun.viewingCurrent = true
    app.data.batchPeakRun.viewingId = null
    await wrapper.vm.$nextTick()
    expect(wrapper.findAll('.pane-message')).toHaveLength(0)
  })
})

describe('PaneBrowserBatchPeaks ledger export', () => {
  it('offers the export from the view menu and launches it', async () => {
    wrapper = await mountPane({ peaks: [peak('bp-1', 'assigned', 0.9)] })

    const button = wrapper.find('.view-popover .export-ledger')
    expect(button.exists()).toBe(true)
    expect(button.attributes('disabled')).toBeUndefined()

    await button.trigger('click')
    await wrapper.vm.$nextTick()
    expect(post).toHaveBeenCalledWith(
      '/batch-peaks/batch/b-1/export',
      {},
      expect.objectContaining({ type: 'export_batch_ledger' })
    )
    // Busy until the task's own notification says it ended.
    expect(useBatchPeakCompute().exporting).toBe(true)
    notificationHandlers.export_batch_ledger?.forEach((cb) =>
      cb({ status: 'success', process_id: 'proc-1' })
    )
    expect(useBatchPeakCompute().exporting).toBe(false)
  })

  it('offers no export on an empty ledger', async () => {
    // The view menu hangs off the tier strip, which an empty ledger does not
    // draw - so there is no control to disable.
    wrapper = await mountPane({ peaks: [] })
    expect(wrapper.find('.export-ledger').exists()).toBe(false)
  })
})

// A batch import that carried the engine's own verdict: the engine's tier
// beside the consensus, the batch peaks where the two differ, and the strip
// read in the engine's words.
describe('PaneBrowserBatchPeaks engine tier', () => {
  const peaks = () => [
    // The engine held it a candidate; the members' measurement says assigned.
    peak('bp-1', 'assigned', 0.9, { mz: 100.1, engine_tier: 'candidate', engine_claim: 'level 3' }),
    // The two agree.
    peak('bp-2', 'assigned', 0.8, { mz: 200.1, engine_tier: 'assigned' }),
    // A formula the engine stated no tier for.
    peak('bp-3', 'candidate', 0.5, { mz: 300.1 }),
    // A peak nobody explained.
    peak('bp-4', 'unassigned', null, { mz: 400.1, consensus_formula: null })
  ]

  const toggle = (id) =>
    wrapper
      .findAllComponents({ name: 'ToggleSwitch' })
      .find((component) => component.props('inputId') === id)
  const ids = () => wrapper.vm.rows.map((row) => row.batch_peak_id)
  const chips = () => wrapper.findAll('.tier-stat').map((chip) => chip.text())

  it('is absent, column and switches, from a ledger no engine tiered', async () => {
    wrapper = await mountPane({ peaks: peaks().map(({ engine_tier, ...bp }) => bp) })

    expect(columnFor('engineTierRank')).toBeUndefined()
    expect(toggle('batch-engine-tier-view')).toBeUndefined()
    expect(toggle('batch-engine-disagrees')).toBeUndefined()
  })

  it("shows the engine's tier in a column of its own, the consensus left as it is", async () => {
    wrapper = await mountPane({ peaks: peaks() })

    expect(columnFor('engineTierRank')).toBeDefined()
    expect(columnFor('consensus_tier')).toBeDefined()
    const byId = Object.fromEntries(wrapper.vm.rows.map((row) => [row.batch_peak_id, row]))
    expect(byId['bp-1'].engineTierRank).toBe(1)
    expect(byId['bp-3'].engineTierRank).toBeNull()
    // Ordered by the consensus, not by the engine.
    expect(ids()).toEqual(['bp-1', 'bp-2', 'bp-3', 'bp-4'])
  })

  it('names both tiers and the claim in the chip tooltip', async () => {
    wrapper = await mountPane({ peaks: peaks() })

    const text = wrapper.vm.engineTierTooltip(peaks()[0])
    expect(text).toContain('candidate')
    expect(text).toContain('assigned')
    expect(text).toContain('level 3')
    expect(wrapper.vm.engineTierTooltip(peaks()[1])).toContain('agrees')
  })

  it('keeps only the batch peaks where the engine disagrees, when asked', async () => {
    wrapper = await mountPane({ peaks: peaks() })

    await toggle('batch-engine-disagrees').trigger('click')

    expect(ids()).toEqual(['bp-1'])
    await toggle('batch-engine-disagrees').trigger('click')
    expect(ids()).toHaveLength(4)
  })

  it("counts and filters the strip by the engine's tier when the view is on", async () => {
    wrapper = await mountPane({ peaks: peaks() })
    expect(chips()).toEqual([
      '2 assigned',
      '1 candidate',
      '0 below',
      '1 unassigned',
      '0 reagent',
      '0 artifact'
    ])
    expect(wrapper.find('.engine-view-label').exists()).toBe(false)

    await toggle('batch-engine-tier-view').trigger('click')

    expect(chips()).toEqual([
      '1 assigned',
      '1 candidate',
      '0 below',
      '1 unassigned',
      '1 not stated',
      '0 reagent',
      '0 artifact'
    ])
    expect(wrapper.find('.engine-view-label').exists()).toBe(true)
    wrapper.vm.toggleTier('candidate')
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-1'])
    wrapper.vm.toggleTier('engine_unstated')
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-3'])
  })

  // The tier column shows the consensus in either view, so its own filter
  // filters the consensus; only the chips read the engine's tier.
  it("keeps the tier column's filter on the consensus in the engine-tier view", async () => {
    wrapper = await mountPane({ peaks: peaks() })
    await toggle('batch-engine-tier-view').trigger('click')

    expect(wrapper.vm.BUCKETS).not.toContain('engine_unstated')
    wrapper.vm.filters.consensus_tier.constraints[0].value = 'assigned'
    await wrapper.vm.$nextTick()
    // bp-1 is the engine's candidate, but the consensus says assigned.
    expect(ids()).toEqual(['bp-1', 'bp-2'])

    // A chip filters the engine's tier on top, without rewriting the column's.
    wrapper.vm.toggleTier('candidate')
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-1'])
    expect(wrapper.vm.filters.consensus_tier.constraints[0].value).toBe('assigned')
    expect(wrapper.find('.tier-stat.candidate').classes()).toContain('active')
    expect(wrapper.find('.tier-stat.assigned').classes()).not.toContain('active')

    // Leaving the view drops both, and coming back brings neither back.
    await toggle('batch-engine-tier-view').trigger('click')
    expect(ids()).toHaveLength(4)
    expect(wrapper.vm.filters.consensus_tier.constraints[0].value).toBeNull()
    await toggle('batch-engine-tier-view').trigger('click')
    expect(ids()).toHaveLength(4)
  })

  // Both switches outlive the ledger they were flipped on; one no engine tiered
  // has nothing to read in its words, and no switch left to turn them off.
  it('reads the consensus again on a ledger no engine tiered, whatever the switches', async () => {
    wrapper = await mountPane({ peaks: peaks() })
    await toggle('batch-engine-tier-view').trigger('click')
    await toggle('batch-engine-disagrees').trigger('click')

    app.data.batchPeak.list = peaks().map(({ engine_tier, ...bp }) => bp)
    await wrapper.vm.$nextTick()

    expect(chips()).not.toContain('0 not stated')
    expect(chips()[0]).toBe('2 assigned')
    expect(ids()).toHaveLength(4)
  })

  it('drops a chip filter when the view changes', async () => {
    wrapper = await mountPane({ peaks: peaks() })
    wrapper.vm.toggleTier('candidate')
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-3'])

    await toggle('batch-engine-tier-view').trigger('click')

    expect(ids()).toHaveLength(4)
  })
})

describe('PaneBrowserBatchPeaks on a batch of two instrument types', () => {
  const ORBI = peak('bp-orbi', 'assigned', 0.9, {
    instrument_type: 'orbi',
    intensity_variable: 'sum_peak_heights'
  })
  const TOF = peak('bp-tof', 'candidate', 0.5, {
    instrument_type: 'tof',
    intensity_variable: 'sum_peak_areas'
  })
  // The store's partition, as the real one keeps it: a choice, and the rows in it.
  const partitioned = () => ({
    instrumentTypes: ['orbi', 'tof'],
    instrumentType: 'orbi',
    inPartition: (bp) => bp.instrument_type === app.data.batchPeak.instrumentType,
    showInstrument: vi.fn((type) => {
      app.data.batchPeak.instrumentType = type
    })
  })
  const picker = () => wrapper.findComponent({ name: 'SelectButton' })
  const rowIds = () => wrapper.vm.rows.map((row) => row.batch_peak_id)

  it('offers no picker on a batch of one instrument type', async () => {
    // The real store's answer on a single-type batch: one type, no partition.
    wrapper = await mountPane({ peaks: [ORBI], ledger: { instrumentTypes: ['orbi'] } })
    expect(picker().exists()).toBe(false)
    expect(rowIds()).toEqual(['bp-orbi'])
  })

  it("lists, counts and names the unit of one instrument type's batch peaks at a time", async () => {
    wrapper = await mountPane({ peaks: [ORBI, TOF], ledger: partitioned() })

    expect(picker().props('options')).toEqual([
      { label: 'Orbitrap', value: 'orbi' },
      { label: 'TOF', value: 'tof' }
    ])
    expect(picker().props('modelValue')).toBe('orbi')
    expect(rowIds()).toEqual(['bp-orbi'])
    expect(wrapper.vm.tierChips.find((chip) => chip.key === 'assigned').count).toBe(1)
    expect(wrapper.vm.tierChips.find((chip) => chip.key === 'candidate').count).toBe(0)
    expect(wrapper.vm.breadcrumb.items[1].label).toBe('1 batch peaks')
    // The other type's row is not an isotopologue folded out of sight.
    expect(wrapper.vm.breadcrumb.items[1].tooltip).toBeUndefined()
    expect(wrapper.vm.intensityTooltip).toMatch(/summed peak height/)

    picker().vm.$emit('update:modelValue', 'tof')
    await wrapper.vm.$nextTick()
    expect(app.data.batchPeak.showInstrument).toHaveBeenCalledWith('tof')
    expect(rowIds()).toEqual(['bp-tof'])
    expect(wrapper.vm.intensityTooltip).toMatch(/summed peak area/)
  })

  it("offers the engine tier view only where the shown type's batch peaks carry one", async () => {
    // A batch import lands on one partition: here the Orbitrap one.
    const imported = { ...ORBI, engine_tier: 'assigned' }
    wrapper = await mountPane({ peaks: [imported, TOF], ledger: partitioned() })
    expect(wrapper.vm.hasEngineTiers).toBe(true)

    picker().vm.$emit('update:modelValue', 'tof')
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.hasEngineTiers).toBe(false)
  })
})
