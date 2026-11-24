import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

import { MAX_SELECTED_BATCH_PEAKS } from '@/stores/data/modules/batchPeak/ledger'
import { TIERING_PROVISIONAL } from '@/lib/tiers'
import { LEVELS } from '@/lib/levels'
import { SORT_FIELDS } from '@/lib/ledger/useLedgerView.js'
import { listingName } from '@/lib/referenceListings'
import { runLabel } from '@/stores/data/modules/batchPeak/run.js'

// The tier strip and the tier column's sort: both read the confidence order out
// of @/lib/tiers, and both used to be wrong in the same direction (alphabetical,
// worst tier first). Assign batch is no longer here - it is in the browser's
// switch bar, and its own spec is
// panes/PaneBrowserMatch/batchAssignControl.spec.js - but the refusal it can
// come back with is still rendered below this table, and the empty ledger
// offers the action itself, so those are covered under "assign refusal" and
// "empty ledger" below.

const post = vi.fn()

// The status read the assign store makes on batch focus: none by default,
// since most of this file is about the table.
const get = vi.fn()

vi.mock('@/api', () => ({
  api: { http: { post: (...args) => post(...args), get: (...args) => get(...args) } }
}))

let app
// Callbacks the compute store registered through app.ui.notification.on, by
// type. The pane registers none of its own any more; the entry is kept because
// mounting it creates that store, which does.
let notificationHandlers

vi.mock('@/stores', () => ({ useApp: () => app }))

vi.mock('@/lib/base', async () => ({
  BaseTabbedPanel: { template: '<div><slot name="menu" /><slot /></div>' },
  // Rendered, so the empty ledger's title and its Assign batch can be read.
  BaseEmptyState: {
    props: ['title', 'hint', 'icon'],
    template: '<div class="empty-state">{{ title }}<slot name="actions" /></div>'
  },
  // Rendered, so a test that renders the cells can read the chip's role and
  // the formula a cell would copy.
  // Its cap mark drawn as the chip draws it (BaseTierTag.spec.js pins the
  // chip's own).
  BaseTierTag: {
    name: 'BaseTierTag',
    props: { tier: String, role: String, cap: Object, outlined: Boolean, tooltip: String },
    template:
      '<span class="tier-tag" :data-tier="tier" :data-role="role" ' +
      ':data-outlined="String(outlined)" :data-tooltip="tooltip">' +
      '<span v-if="cap" data-testid="tier-reason-mark" :aria-label="cap.label" ' +
      'v-tooltip.top="cap.tooltip" /></span>'
  },
  BaseCopyableField: {
    props: ['field', 'tooltip'],
    template: '<span class="copyable" :data-tooltip="tooltip">{{ field }}<slot /></span>'
  },
  // Rendered, so a test that renders the cells can read the level and the hover
  // text the pane handed the chip; the chip has a spec of its own.
  BaseLevelTag: {
    name: 'BaseLevelTag',
    props: { level: String, tooltip: String, outlined: Boolean },
    template:
      '<span class="level-tag" :data-level="level" :data-tooltip="tooltip" ' +
      ':data-outlined="String(outlined)" />'
  },
  BaseVerdictBadge: true,
  // Run history's row provenance; the run tests read its ordinal and label alone.
  BaseRunProvenance: true,
  // Rendered, so a failed load's message can be read where the table would be.
  BaseLoadError: {
    props: ['error', 'fallback', 'onRetry'],
    template: '<div class="load-error">{{ fallback }}</div>'
  },
  // Real: the tier header's provisional mark is read off what it draws.
  BaseProvisionalMark: (await vi.importActual('@/lib/base/BaseProvisionalMark.vue')).default,
  // Real: the formula cell's badges are read off the reading and tone they draw.
  BaseEvidenceBadge: (await vi.importActual('@/lib/base/BaseEvidenceBadge.vue')).default
}))
vi.mock('@/lib/panes/PaneBrowserMatch/BatchPeakContinuityPopover.vue', () => ({
  default: {
    name: 'BatchPeakContinuityPopover',
    props: ['row', 'isotopeLine'],
    template: '<div class="continuity-popover-stub" />'
  }
}))
vi.mock('@/lib/ledger/LedgerVerdictPopover.vue', () => ({
  default: { name: 'LedgerVerdictPopover', template: '<div class="verdict-popover-stub" />' }
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
// A ledger row as the server serves it: the one owner link of an isotope
// line (`line_owner_id`) derived as the server derives it, from the members'
// own link and else a stamp, unless the row names one itself.
const served = (row) => ({
  line_owner_id: row.isotopologue_of ?? row.stamped_isotopologue_of?.batch_peak_id ?? null,
  ...row
})

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
        list: peaks.map(served),
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
        viewingId: null,
        // As the real store counts (stores/data/modules/batchPeak/run.js): the
        // list is newest first, the oldest kept is #1, an unlisted run null.
        ordinal(run) {
          const index = this.list.findIndex((r) => r.batch_peak_run_id === run?.batch_peak_run_id)
          return index === -1 ? null : this.list.length - index
        }
      }
    },
    ui: {
      help: helpStub,
      notification: {
        on: (type, callback) => {
          notificationHandlers[type] = [...(notificationHandlers[type] ?? []), callback]
          return { remove: () => {} }
        },
        push: vi.fn(),
        claim: vi.fn()
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
// What the cell popovers were asked to do, by the popover's aria-label, so a
// test can tell the continuity popover from the verdict one.
const popoverCalls = []
const PopoverStub = {
  name: 'Popover',
  // The cell popovers call these; open or not, the stub renders its slot.
  methods: {
    show() {
      popoverCalls.push([this.$attrs['aria-label'], 'show'])
    },
    hide() {
      popoverCalls.push([this.$attrs['aria-label'], 'hide'])
    },
    toggle() {
      popoverCalls.push([this.$attrs['aria-label'], 'toggle'])
    }
  },
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
  // The filter bar's search box, typed into as a reader would.
  IconField: { template: '<span class="icon-field"><slot /></span>' },
  InputIcon: { template: '<span class="input-icon"><slot /></span>' },
  InputText: {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<input class="search-input" :value="modelValue" ' +
      '@input="$emit(\'update:modelValue\', $event.target.value)" />'
  },
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
// Run history, read beside the banner: the two name a run alike.
const { default: BatchRunHistory } =
  await import('@/lib/panes/PaneBrowserMatch/BatchRunHistory.vue')
const { useBatchAssign } = await import('@/lib/panes/PaneBrowserMatch/stores/batchAssign.js')

async function mountPane(options = {}) {
  // The app facade first: the assign store the pane reads its refusal off is
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
  get.mockReset()
  get.mockResolvedValue(null)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
  vi.clearAllMocks()
})

// Renders a column's cells through a stub that hands each its row.
async function cellsOf(field, rows, verdicts = {}) {
  const tableRows = ref([])
  app = makeApp({ peaks: rows, verdicts })
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

describe('PaneBrowserBatchPeaks assign refusal', () => {
  // The button is a row up, in the switch bar, but the refusal it comes back
  // with is reported here - below the table it is about, rather than in a toast
  // that has scrolled away by the time the user looks up.
  it('reports a refused launch below the table', async () => {
    post.mockRejectedValue(apiError(403, 'You do not have permission.'))
    wrapper = await mountPane()

    await useBatchAssign().assign()
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.pane-message')).toHaveLength(1)
    expect(wrapper.find('.pane-message').text()).toContain('do not have permission')
  })

  // Whatever was in flight or went wrong belonged to the previous batch. The
  // pane owns this because it owns the rest of the per-batch reset beside it.
  it('clears the launch state when the focused batch changes', async () => {
    post.mockRejectedValue(apiError(500, 'boom'))
    wrapper = await mountPane()
    const assign = useBatchAssign()

    await assign.assign()
    await wrapper.vm.$nextTick()
    expect(wrapper.findAll('.pane-message')).toHaveLength(1)

    app.data.batch.focusedId = 'b-2'
    await wrapper.vm.$nextTick()

    expect(assign.launchError).toBeNull()
    expect(assign.launching).toBe(false)
    expect(wrapper.findAll('.pane-message')).toHaveLength(0)
  })
})

describe('PaneBrowserBatchPeaks empty ledger', () => {
  // The table's #empty slot, rendered where the table has no rows.
  const EmptyTable = {
    ...DataTableStub,
    template: '<div class="datatable"><slot v-if="!value?.length" name="empty" /><slot /></div>'
  }
  const ButtonStub = {
    props: ['label', 'disabled', 'loading'],
    template: '<button :class="$attrs.class" :disabled="disabled">{{ label }}</button>'
  }
  async function mountEmpty(options = {}) {
    app = makeApp(options)
    localStorage.clear()
    setActivePinia(createPinia())
    wrapper = mount(PaneBrowserBatchPeaks, {
      global: {
        stubs: { ...GLOBAL_STUBS, DataTable: EmptyTable, Button: ButtonStub },
        directives: { tooltip: {}, help: {} }
      }
    })
    await flushPromises()
    return wrapper
  }

  // A ledger with no batch peaks offers Assign batch itself, and the bar
  // hides its own copy then (BatchAssignControl reads the same computed).
  it('offers Assign batch, and posts the pass from it', async () => {
    await mountEmpty({ peaks: [] })

    const button = wrapper.find('.empty-assign')
    expect(button.exists()).toBe(true)
    expect(button.text()).toBe('Assign batch')
    expect(wrapper.text()).toContain('Not assigned')
    expect(wrapper.text()).not.toContain('Rebuild batch ledger')

    await button.trigger('click')
    await flushPromises()
    expect(post).toHaveBeenCalledWith(
      '/batch-peaks/batch/b-1/assign',
      {},
      expect.objectContaining({ type: 'assign_batch' })
    )
  })

  it('disables it for a viewer', async () => {
    await mountEmpty({ peaks: [], workspace: { ...WORKSPACE, my_role: 'guest' } })

    expect(wrapper.find('.empty-assign').attributes('disabled')).toBeDefined()
  })

  it('offers no Assign batch while a pass runs on the empty ledger', async () => {
    get.mockResolvedValue({ sample_batch_id: 'b-1', state: 'running', running: {}, stages: [] })
    await mountEmpty({ peaks: [] })

    expect(wrapper.find('.empty-assign').exists()).toBe(false)
  })

  // A ledger with rows offers it in the bar, never here: a table the filters
  // emptied is not an unassigned batch.
  it('leaves Assign batch to the bar once the ledger holds batch peaks', async () => {
    await mountEmpty({ peaks: [peak('bp-1', 'assigned', 0.9)] })

    expect(useBatchAssign().ledgerOffersAssign).toBe(false)
  })
})

describe('PaneBrowserBatchPeaks tier strip', () => {
  const peaks = [
    peak('bp-1', 'assigned', 0.9),
    peak('bp-2', 'assigned', 0.7),
    peak('bp-3', 'candidate', 0.5),
    peak('bp-4', 'unassigned', null)
  ]

  it('renders a chip per tier with rows, in confidence order, with counts', async () => {
    wrapper = await mountPane({ peaks })
    const chips = wrapper.findAll('.tier-stat')

    // A chip with nothing in it is not drawn.
    expect(chips.map((chip) => chip.text())).toEqual(['2 assigned', '1 candidate', '1 unassigned'])
    expect(wrapper.find('[data-testid="role-chips"]').exists()).toBe(false)
  })

  // The bar over the chips: the tiers' shares, in their order.
  it('draws the tier bar over the chips', async () => {
    wrapper = await mountPane({ peaks })
    const segments = wrapper.findAll('[data-testid="tier-bar"] .tier-segment')

    expect(segments.map((segment) => segment.attributes('data-share'))).toEqual([
      '0.5000',
      '0.2500',
      '0.2500'
    ])
  })

  // The ledger's count is in its header line, in species.
  it('counts the batch peaks in its header line', async () => {
    wrapper = await mountPane({ peaks })

    expect(wrapper.find('[data-testid="ledger-count"]').text()).toBe('4 batch peaks')
  })

  it('counts nothing while the batch has no batch peaks', async () => {
    wrapper = await mountPane({ peaks: [] })

    expect(wrapper.find('[data-testid="ledger-count"]').exists()).toBe(false)
  })

  // The store keeps the last good load's rows when a reload fails (or the
  // next batch's load does). Their count, in the header or on the chips,
  // would read as this load's.
  it('shows no count and no chip counts after a failed reload', async () => {
    wrapper = await mountPane({ peaks })
    app.data.batchPeak.error = new Error('boom')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.load-error').text()).toBe('Could not load the batch peaks.')
    expect(wrapper.find('[data-testid="ledger-count"]').exists()).toBe(false)
    expect(wrapper.findAll('.tier-stat').map((chip) => chip.text())).toEqual([
      'assigned',
      'candidate',
      'unassigned'
    ])
  })

  // A batch with no batch peaks draws no strip; a first load that failed
  // still does, with the view menu, as the docs say.
  it('keeps the strip and the view menu after a failed first load', async () => {
    wrapper = await mountPane({ peaks: [], ledger: { error: new Error('boom') } })

    expect(wrapper.find('.load-error').exists()).toBe(true)
    expect(wrapper.find('.tier-strip').exists()).toBe(true)
    expect(wrapper.find('[data-testid="ledger-search"]').exists()).toBe(true)
    expect(wrapper.find('.view-menu-button').exists()).toBe(true)
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

    await wrapper.find('.tier-stat.reagent').trigger('click')
    expect([...wrapper.vm.activeTiers]).toEqual(['reagent'])
    expect(wrapper.vm.rows.map((row) => row.batch_peak_id)).toEqual(['bp-br'])
  })

  // Multi-select, as the sample ledger's: the Tier column's filter menu, which
  // held one tier and listed the API's keys, is gone.
  it('filters the table by every chip that is on', async () => {
    wrapper = await mountPane({ peaks })

    await wrapper.findAll('.tier-stat')[1].trigger('click')
    expect([...wrapper.vm.activeTiers]).toEqual(['candidate'])
    expect(wrapper.vm.rows.map((row) => row.consensus_tier)).toEqual(['candidate'])

    await wrapper.findAll('.tier-stat')[2].trigger('click')
    expect([...wrapper.vm.activeTiers]).toEqual(['candidate', 'unassigned'])
    expect(wrapper.vm.rows.map((row) => row.consensus_tier).sort()).toEqual([
      'candidate',
      'unassigned'
    ])
    expect(wrapper.vm.filters.consensus_tier).toBeUndefined()
  })

  it('clears the filter when the active chip is clicked again', async () => {
    wrapper = await mountPane({ peaks })
    const candidate = wrapper.findAll('.tier-stat')[1]

    await candidate.trigger('click')
    await candidate.trigger('click')

    expect(wrapper.vm.activeTiers.size).toBe(0)
    expect(wrapper.vm.rows).toHaveLength(peaks.length)
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
    // An artifact names no ion, and a peak nothing explained no formula: both
    // cells are blank, as an empty cell is in both scopes, and the chip says
    // what the peak is.
    expect(byId['bp-ring'].text()).toBe('')
    expect(byId['bp-bare'].text()).toBe('')
  })

  it('hands the role to the chip', async () => {
    wrapper = await rendered([REAGENT, BARE])
    const byId = Object.fromEntries(
      wrapper.vm.rows.map((row, index) => [
        row.batch_peak_id,
        cells('tierRank')[index].find('.tier-tag')
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

  it('finds the ion with the search', async () => {
    wrapper = await mountPane({ peaks: [REAGENT, peak('bp-a', 'assigned', 0.9)] })

    await wrapper.find('[data-testid="ledger-search"]').setValue('Br')
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
      'Compound',
      'Ion',
      'Max intensity',
      'Tier',
      // The tiering is still being built, and the tier header says so.
      TIERING_PROVISIONAL.label,
      'Samples',
      'Across the batch',
      '' // the verdict header is an icon
    ])
    for (const [, tip] of tips) expect(tip.length).toBeGreaterThan(20)
    expect(tips.find(([label]) => label === TIERING_PROVISIONAL.label)[1]).toBe(
      TIERING_PROVISIONAL.tooltip
    )
    expect(tips.find(([label]) => label === 'm/z')[1]).toMatch(/anchor/i)
    expect(tips.find(([label]) => label === 'Max intensity')[1]).toMatch(/any sample/)
    expect(tips.find(([label]) => label === 'Compound')[1]).toMatch(/consensus/i)
    expect(tips.find(([label]) => label === 'Ion')[1]).toMatch(/channels/)
  })

  it('sorts the tier column on the rank, which is what the header click reads', async () => {
    // The pre-sorted `rows` above are only the tie-break; a header click sorts
    // on the column's own sort field, and the bug this fixes was that field
    // being the raw tier string.
    wrapper = await mountPane()

    const column = columnFor('tierRank')
    expect(column.props('sortField') ?? column.props('field')).toBe('tierRank')
    expect(column.props('sortable')).toBe(true)
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
    // Falling back to `field` the way PrimeVue does.
    const column = columnFor('tierRank')
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

  // The tier chips are the one tier filter, multi-select as the sample
  // ledger's: the column's menu, which held a single tier and listed the API's
  // keys, is gone.
  it('carries no tier filter menu beside the chips', async () => {
    wrapper = await mountPane()

    expect(Object.keys(wrapper.vm.filters)).not.toContain('consensus_tier')
    expect(Object.keys(wrapper.vm.filters)).not.toContain('tierRank')
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

  // The listing is the Compound cell's second line, the Listed as column gone
  // (docs/dev/assignment_ledger_and_card.md, section 3.2).
  const listingColumn = () =>
    wrapper
      .findAll('.column-stub')
      .find((col) => col.attributes('data-field') === 'consensus_formula')

  // No flask: the name is written out, cut short, and the list is left to
  // the hover.
  it('folds to the name, cut short, and the tags by default', async () => {
    wrapper = await renderedCells([
      peak('bp-1', 'assigned', 0.9, { reference_listing: LISTING }),
      peak('bp-2', 'assigned', 0.8, { mz: 200.1 })
    ])
    const [listed, unlisted] = listingColumn().findAll('.stub-cell')

    expect(listingColumn().find('.ph-flask').exists()).toBe(false)
    expect(listed.find('[data-testid="listing-folded"]').text()).toBe(
      'decamethylcyclopentasiloxane +1'
    )
    expect(listed.find('[data-testid="listing-folded"]').classes()).toContain('folded')
    expect(listed.find('.listing-source').exists()).toBe(false)
    expect(listed.find('[data-testid="list-tag-background"]').exists()).toBe(true)
    expect(unlisted.find('[data-testid="listed-as"]').exists()).toBe(false)
    expect(unlisted.find('.copyable').text()).toBe('C6H12O6')
  })

  it("writes the column out from the overflow menu's switch", async () => {
    wrapper = await renderedCells([peak('bp-1', 'assigned', 0.9, { reference_listing: LISTING })])
    const toggle = wrapper.find('#batch-listing-full')

    expect(toggle.text()).toBe('false')
    await toggle.trigger('click')
    const listed = listingColumn().find('.stub-cell')
    expect(listed.find('.listing-name').text()).toBe('decamethylcyclopentasiloxane +1')
    expect(JSON.parse(localStorage.getItem('mascope.browser.match.ledgerView')).listingFull).toBe(
      true
    )
  })

  it('shows the name, the list and its tag on a listed batch peak, in full', async () => {
    wrapper = await renderedCells(
      [
        peak('bp-1', 'assigned', 0.9, { reference_listing: LISTING }),
        peak('bp-2', 'assigned', 0.8, { mz: 200.1 })
      ],
      { full: true }
    )
    const [listed, unlisted] = listingColumn().findAll('.stub-cell')

    // Under the formula, in the Compound cell.
    const compound = listed.find('[data-testid="compound-cell"]')
    expect(compound.find('.compound-formula .copyable').text()).toBe('C6H12O6')
    expect(compound.find('.compound-listing .listing-name').text()).toBe(
      'decamethylcyclopentasiloxane +1'
    )
    expect(listed.find('.listing-source').text()).toBe('cyclic-siloxanes')
    expect(listed.find('[data-testid="list-tag-background"]').exists()).toBe(true)
    expect(unlisted.find('[data-testid="listed-as"]').exists()).toBe(false)
  })

  it('reads the listing onto the row, with no column of its own', async () => {
    wrapper = await mountPane({
      peaks: [
        peak('bp-1', 'assigned', 0.9, { reference_listing: LISTING }),
        peak('bp-2', 'assigned', 0.8, { mz: 200.1 })
      ]
    })
    const rows = new Map(wrapper.vm.rows.map((row) => [row.batch_peak_id, row]))

    expect(listingName(rows.get('bp-1').listing)).toBe('decamethylcyclopentasiloxane +1')
    expect(rows.get('bp-2').listing).toBeNull()
    expect(columnFor('listingName')).toBeUndefined()
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

  it("reads the consensus's target as target and its name", async () => {
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
    expect(unlisted.find('[data-testid="listed-as"]').exists()).toBe(false)
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
  async function formulaCells(peaks, field = 'consensus_formula') {
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
      .find((col) => col.attributes('data-field') === field)
      .findAll('.stub-cell')
  }

  // The batch's readings are badges in Across the batch.
  it('marks a row a series vouches for, and says which', async () => {
    const cells = await formulaCells(
      [
        peak('bp-1', 'candidate', 0.7, { series: SERIES }),
        peak('bp-2', 'assigned', 0.9, { mz: 200.1, series: null })
      ],
      'acrossRank'
    )
    const marks = cells
      .map((cell) => cell.find('[data-testid="mark-series"]'))
      .filter((m) => m.exists())
    expect(cells).toHaveLength(2)
    expect(marks).toHaveLength(1)
    expect(marks[0].attributes('aria-label')).toBe(
      "In the batch's CH2 series: one CH2 heavier than C5H10O6, which the batch assigns."
    )
    expect(marks[0].attributes('data-tone')).toBe('supports')
    expect(marks[0].find('.ph-stairs').exists()).toBe(true)
  })

  // A row curated for the whole batch: the hand, in the human tone - an
  // outline in the text colour, never the accent.
  it('marks a row curated by hand with the hand, in the human tone', async () => {
    const cells = await formulaCells(
      [
        peak('bp-1', 'assigned', 0.9, { curated: true }),
        peak('bp-2', 'assigned', 0.9, { mz: 200.1 })
      ],
      'acrossRank'
    )
    const hand = cells[0].find('[data-testid="mark-manual"]')
    expect(hand.attributes('aria-label')).toBe('Curated by hand for the whole batch')
    expect(hand.attributes('data-tone')).toBe('human')
    expect(hand.find('.ph-hand-pointing').exists()).toBe(true)
    expect(cells[1].find('[data-testid="mark-manual"]').exists()).toBe(false)
  })

  // Across the batch (docs/dev/assignment_ledger_and_card.md, section 3.2):
  // at most three badges, the ones that hold the reading back first, the rest
  // in the registry's order, then "+n" naming the others on hover. The
  // formula's cell carries none.
  it('draws at most three badges, what holds it back first, then +n', async () => {
    const rows = [
      peak('bp-1', 'candidate', 0.7, {
        max_intensity: 1000,
        curated: true,
        series: SERIES,
        blend: { formulas: ['C6H12O6', 'C7H16O5'], axis: 'time', time: null, intensity: null },
        co_variation: { verdict: 'co_varying', r: 0.8, n_spectra: 20 },
        batch_checks: { halogen_lock: { element: 'Br', verdict: 'locked' } },
        continuity: { pairs: [] },
        continuity_grade: 'stable'
      }),
      peak('bp-2', 'assigned', 0.9, {
        mz: 200.1,
        max_intensity: 500,
        series: SERIES,
        curated: true
      }),
      peak('bp-3', 'assigned', 0.9, { mz: 210.1, max_intensity: 100 })
    ]
    const cells = await formulaCells(rows, 'acrossRank')
    const marks = (cell) =>
      cell.findAll('.evidence-badge').map((badge) => badge.attributes('data-mark'))
    expect(marks(cells[0])).toEqual(['blend', 'series', 'coVariation'])
    expect(cells[0].find('[data-testid="across-more"]').text()).toBe('+3')
    expect(wrapper.vm.acrossMore(wrapper.vm.rows[0]).map((m) => m.id)).toEqual([
      'ratio',
      'facts',
      'manual'
    ])
    expect(wrapper.vm.acrossMoreTooltip(wrapper.vm.rows[0]).split('\n')).toHaveLength(3)
    // Two badges: both, and no count.
    expect(marks(cells[1])).toEqual(['series', 'manual'])
    expect(cells[1].find('[data-testid="across-more"]').exists()).toBe(false)
    // None: the cell is empty.
    expect(cells[2].findAll('.evidence-badge')).toHaveLength(0)

    wrapper.unmount()
    const formula = await formulaCells(rows)
    expect(formula[0].findAll('.evidence-badge')).toHaveLength(0)
  })

  // The column sorts the rows most held back first, then the most badges, a
  // row with none last.
  it('sorts Across the batch on what holds it back, then on the count', async () => {
    wrapper = await mountPane({
      peaks: [
        peak('bp-none', 'assigned', 0.9, { mz: 100 }),
        peak('bp-one', 'assigned', 0.9, { mz: 110, series: SERIES }),
        peak('bp-held', 'candidate', 0.7, {
          mz: 120,
          blend: { formulas: ['C6H12O6', 'C7H16O5'], axis: 'time', time: null, intensity: null }
        }),
        peak('bp-two', 'assigned', 0.9, { mz: 130, series: SERIES, curated: true })
      ]
    })
    expect(columnFor('acrossRank').props('sortable')).toBe(true)
    wrapper.vm.sortField = 'acrossRank'
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.rows.map((row) => row.batch_peak_id)).toEqual([
      'bp-held',
      'bp-two',
      'bp-one',
      'bp-none'
    ])
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
    // The header line's count is species, the folded line under its parent.
    expect(wrapper.vm.countTooltip).toBe(
      '2 species; 1 isotopologue peak shown under their main peak'
    )
    const line = wrapper.vm.rows[1]
    // Named by the substitution the stamp names, in the sample scope's
    // brackets, rather than by its offset (M+2).
    expect(wrapper.vm.childLabel(line)).toBe('[81Br]')
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

  it('folds a row under the one owner link the server names, and no other', async () => {
    // The server states one owner per isotope line (line_owner_id, MAS-865),
    // which the continuity pass pairs the row with too: the ledger follows it
    // whatever the row's other links say.
    const ELSEWHERE = peak('bp-m0', 'assigned', 0.9, { mz: 139.5, consensus_formula: 'C9H8' })
    const line = { ...LINE, isotopologue_of: 'bp-m0', line_owner_id: 'bp-p' }
    wrapper = await mountPane({ peaks: [line, PARENT, ELSEWHERE] })
    expect(wrapper.vm.isotopologueCount(PARENT)).toBe(1)
    expect(wrapper.vm.isotopologueCount(ELSEWHERE)).toBe(0)
    const alone = { ...LINE, line_owner_id: null }
    wrapper = await mountPane({ peaks: [alone, PARENT] })
    expect(wrapper.vm.isotopologueCount(PARENT)).toBe(0)
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
  async function formulaCells(peaks, field = 'consensus_formula') {
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
      .find((col) => col.attributes('data-field') === field)
    return Object.fromEntries(
      column.findAll('.stub-cell').map((cell) => [cell.attributes('data-row'), cell])
    )
  }

  // The line's label is its substitution, the stamp's: the tag that sat beside
  // an "M+2" is the label itself now.
  // A line no stamp names, but the family's own record of its predicted lines
  // does: named by its substitution too, with no stamp's mark. A line nothing
  // names keeps its offset.
  it("names a line the family's record names, and keeps the offset where nothing does", async () => {
    const FOUND = peak('bp-found', 'assigned', 0.7, {
      mz: 140.94,
      consensus_formula: 'C2H4O2',
      isotopologue_of: 'bp-p'
    })
    const BARE_LINE = peak('bp-bare', 'assigned', 0.6, { mz: 139.94, isotopologue_of: 'bp-p' })
    const parent = {
      ...PARENT,
      diagnostic_isotopologues: isotopologues({
        predicted: [{ label: '18O', batch_peak_id: 'bp-found', stamped: false, verdict: 'stamped' }]
      })
    }
    const cells = await formulaCells([parent, FOUND, BARE_LINE])
    const found = cells['bp-found'].find('.child-label')
    expect(found.text()).toBe('[18O]')
    expect(found.attributes('data-testid')).toBeUndefined()
    expect(cells['bp-bare'].find('.child-label').text()).toBe('M+1')
    // A line no stamp or strip names says on hover what it folds as.
    const bare = wrapper.vm.rows.find((row) => row.batch_peak_id === 'bp-bare')
    expect(wrapper.vm.lineTooltip(bare)).toBe('C6H12O6')
  })

  it('names an unfolded stamped line by its isotope, in italics where the stamping predicted it', async () => {
    const PICKED = peak('bp-picked', 'assigned', 0.8, {
      mz: 140.9,
      isotopologue_of: 'bp-p',
      stamped_isotopologue_of: stamp('bp-p', { label: '37Cl', n_predicted: 0, n_picker: 6 })
    })
    const cells = await formulaCells([PARENT, LINE, PICKED, OTHER])
    const tag = cells['bp-line'].find('[data-testid="stamp-tag"]')
    expect(tag.text()).toBe('[81Br]')
    expect(tag.classes()).toContain('child-label')
    expect(tag.classes()).toContain('stamped')
    expect(tag.classes()).toContain('predicted')
    // Its hover is the stamp's account.
    const line = wrapper.vm.rows.find((row) => row.batch_peak_id === 'bp-line')
    expect(wrapper.vm.lineTooltip(line)).toMatch(/^Stamped as the 81Br isotopologue of C2H4O2/)
    const picked = cells['bp-picked'].find('[data-testid="stamp-tag"]')
    expect(picked.text()).toBe('[37Cl]')
    expect(picked.classes()).not.toContain('predicted')
    expect(cells['bp-other'].find('[data-testid="stamp-tag"]').exists()).toBe(false)
  })

  it('marks a parent the batch shows an isotopologue of, supporting once one is stamped', async () => {
    const FOUND = peak('bp-found', 'assigned', 0.9, {
      mz: 250.1,
      diagnostic_isotopologues: isotopologues({ n_found: 1, n_stamped: 0 })
    })
    const NONE = peak('bp-none', 'assigned', 0.9, {
      mz: 260.1,
      diagnostic_isotopologues: isotopologues({ n_found: 0, n_stamped: 0 })
    })
    const cells = await formulaCells([PARENT, LINE, FOUND, NONE, OTHER], 'acrossRank')
    const mark = cells['bp-p'].find('[data-testid="mark-isotopes"]')
    expect(mark.attributes('aria-label')).toBe('1 of 2 isotope lines stamped')
    expect(mark.attributes('data-tone')).toBe('supports')
    const found = cells['bp-found'].find('[data-testid="mark-isotopes"]')
    expect(found.exists()).toBe(true)
    // Found, none held: shown, and it supports nothing.
    expect(found.attributes('data-tone')).toBe('neutral')
    // Nothing found: the record says so on hover elsewhere, no mark here.
    expect(cells['bp-none'].find('[data-testid="mark-isotopes"]').exists()).toBe(false)
    expect(cells['bp-other'].find('[data-testid="mark-isotopes"]').exists()).toBe(false)
  })
})

describe('PaneBrowserBatchPeaks probable blends', () => {
  const blend = {
    formulas: ['C10H16O4', 'C9H12N2O2'],
    ions: ['C10H15O4-', 'C9H12N3O5-'],
    n: [40, 12],
    axis: 'time',
    intensity: null,
    time: { p: 1e-6, runs: 2, expected_runs: 19.5, separates: true, earlier: 'C9H12N2O2' }
  }
  const BLENDED = peak('bp-b', 'candidate', 0.6, {
    mz: 199.0976,
    consensus_formula: 'C10H16O4',
    blend
  })
  const OTHER = peak('bp-other', 'candidate', 0.5, { mz: 300.2, consensus_formula: 'C12H18O5' })
  const ids = () => wrapper.vm.rows.map((row) => row.batch_peak_id)

  async function formulaCells(peaks, field = 'consensus_formula') {
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
      .find((col) => col.attributes('data-field') === field)
    return Object.fromEntries(
      column.findAll('.stub-cell').map((cell) => [cell.attributes('data-row'), cell])
    )
  }

  it('marks a blended batch peak beside its formula, and no other', async () => {
    const cells = await formulaCells([BLENDED, OTHER], 'acrossRank')
    const mark = cells['bp-b'].find('[data-testid="mark-blend"]')
    expect(mark.exists()).toBe(true)
    expect(mark.attributes('aria-label')).toBe(
      'Probable blend of C10H16O4 and C9H12N2O2, parted by acquisition time'
    )
    expect(mark.attributes('data-tone')).toBe('holds-back')
    expect(cells['bp-other'].find('[data-testid="mark-blend"]').exists()).toBe(false)
  })

  it('keeps only the probable blends under the filter', async () => {
    wrapper = await mountPane({ peaks: [BLENDED, OTHER] })
    expect(wrapper.vm.hasBlends).toBe(true)
    expect(ids()).toEqual(['bp-b', 'bp-other'])
    wrapper.vm.onlyBlends = true
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-b'])
  })

  it('keeps the family of a blended isotopologue whose parent is no blend', async () => {
    const parent = peak('bp-m0', 'candidate', 0.6, { mz: 199.0976, consensus_formula: 'C10H16O4' })
    const child = peak('bp-m1', 'candidate', 0.6, {
      mz: 200.101,
      consensus_formula: 'C10H16O4',
      isotopologue_of: 'bp-m0',
      blend
    })
    wrapper = await mountPane({ peaks: [parent, child, OTHER] })
    wrapper.vm.showIsotopologues = true
    expect(wrapper.vm.hasBlends).toBe(true)
    wrapper.vm.onlyBlends = true
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-m0', 'bp-m1'])
  })

  it('offers no filter, and filters nothing, where no batch peak is a blend', async () => {
    wrapper = await mountPane({ peaks: [{ ...BLENDED, blend: null }, OTHER] })
    expect(wrapper.vm.hasBlends).toBe(false)
    wrapper.vm.onlyBlends = true
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-b', 'bp-other'])
  })
})

describe('PaneBrowserBatchPeaks flat series', () => {
  const record = (verdict, extra = {}) => ({
    formula: 'C2H3N3',
    verdict,
    cv: verdict === 'flat' ? 0.08 : 0.61,
    n_present: 22,
    n_spectra: 24,
    share: 0.9167,
    normaliser: 'reagent',
    normalised: true,
    source_ion: null,
    ...extra
  })
  const FLAT = peak('bp-f', 'assigned', 0.9, {
    mz: 111.0427,
    consensus_formula: 'C2H3N3',
    flat_series: record('flat')
  })
  const SOURCE = peak('bp-s', 'assigned', 0.9, {
    mz: 45.9935,
    consensus_formula: 'NO2',
    flat_series: record('flat', {
      formula: 'NO2',
      source_ion: { ion_formula: 'NO2-', label: '[NO2]-' }
    })
  })
  const VARYING = peak('bp-v', 'assigned', 0.9, {
    mz: 185.1,
    consensus_formula: 'C10H16O3',
    flat_series: record('varying')
  })
  const OTHER = peak('bp-other', 'candidate', 0.5, { mz: 300.2, consensus_formula: 'C12H18O5' })
  const ids = () => wrapper.vm.rows.map((row) => row.batch_peak_id)

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
        directives: {
          tooltip: {
            mounted: (el, binding) => el.setAttribute('data-tooltip', binding.value ?? ''),
            updated: (el, binding) => el.setAttribute('data-tooltip', binding.value ?? '')
          },
          help: {}
        }
      }
    })
    await wrapper.vm.$nextTick()
    const column = (field) =>
      Object.fromEntries(
        wrapper
          .findAll('.column-stub')
          .find((col) => col.attributes('data-field') === field)
          .findAll('.stub-cell')
          .map((cell) => [cell.attributes('data-row'), cell])
      )
    return { formula: column('acrossRank'), tier: column('tierRank') }
  }

  it('marks a flat batch peak beside its formula, and moves no tier', async () => {
    const { formula, tier } = await formulaCells([FLAT, SOURCE, VARYING, OTHER])
    const mark = formula['bp-f'].find('[data-testid="mark-flatSeries"]')
    expect(mark.attributes('aria-label')).toBe('Flat series: cv 0.08 in 92 % of the samples')
    // It moves nothing, and its colour says so.
    expect(mark.attributes('data-tone')).toBe('neutral')
    expect(mark.attributes('data-tooltip')).toMatch(/the tier stands\.$/)
    const source = formula['bp-s'].find('[data-testid="mark-flatSeries"]')
    expect(source.attributes('aria-label')).toMatch(/a \[NO2\]- ion of the source$/)
    expect(source.attributes('data-tooltip')).toMatch(/The source's list names its ion, \[NO2\]-/)
    // A varying trace, and none read, carry no mark.
    expect(formula['bp-v'].find('[data-testid="mark-flatSeries"]').exists()).toBe(false)
    expect(formula['bp-other'].find('[data-testid="mark-flatSeries"]').exists()).toBe(false)
    // No reason by the tier: the flag is not one.
    expect(tier['bp-f'].find('[data-testid="tier-reason-mark"]').exists()).toBe(false)
  })

  it('keeps only the flat series under the filter', async () => {
    wrapper = await mountPane({ peaks: [FLAT, VARYING, OTHER] })
    expect(wrapper.vm.hasFlatSeries).toBe(true)
    expect(ids()).toEqual(expect.arrayContaining(['bp-f', 'bp-v', 'bp-other']))
    wrapper.vm.onlyFlatSeries = true
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-f'])
  })

  it('offers no filter, and filters nothing, where no batch peak is flat', async () => {
    wrapper = await mountPane({ peaks: [VARYING, OTHER] })
    expect(wrapper.vm.hasFlatSeries).toBe(false)
    wrapper.vm.onlyFlatSeries = true
    await wrapper.vm.$nextTick()
    expect(ids()).toHaveLength(2)
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

  async function formulaCells(peaks, field = 'consensus_formula', { tooltips = false } = {}) {
    const record = (el, binding) => el.setAttribute('data-tooltip', binding.value ?? '')
    const tooltip = tooltips ? { mounted: record, updated: record } : {}
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
        directives: { tooltip, help: {} }
      }
    })
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    const column = wrapper
      .findAll('.column-stub')
      .find((col) => col.attributes('data-field') === field)
    return Object.fromEntries(
      column.findAll('.stub-cell').map((cell) => [cell.attributes('data-row'), cell])
    )
  }

  it('names an unfolded stripped row by its line, and marks the compound that took it', async () => {
    const TWO = { ...PARENT, merged_isotopologues: took(2) }
    const cells = await formulaCells([TWO, LINE, OTHER])
    const across = async () => {
      wrapper.unmount()
      return formulaCells([TWO, LINE, OTHER], 'acrossRank')
    }
    const tag = cells['bp-x'].find('[data-testid="strip-tag"]')
    expect(tag.text()).toBe('[13C]')
    expect(tag.classes()).toContain('stripped')
    expect(tag.classes()).not.toContain('stamped')
    const row = wrapper.vm.rows.find((r) => r.batch_peak_id === 'bp-x')
    expect(wrapper.vm.lineTooltip(row)).toMatch(/^Stripped from the ledger as the 13C isotopologue/)
    expect(cells['bp-other'].find('[data-testid="strip-tag"]').exists()).toBe(false)
    // The rows it took are its isotope lines, the same reading as the stamp's.
    const badges = await across()
    const mark = badges['bp-p'].find('[data-testid="mark-isotopes"]')
    expect(mark.attributes('aria-label')).toBe("2 rows stripped to this compound's isotope lines")
    expect(mark.attributes('data-tone')).toBe('supports')
    expect(badges['bp-other'].find('[data-testid="mark-isotopes"]').exists()).toBe(false)
  })

  // The known-species decision's mark sits beside the strip's in the same cell:
  // a reagent decided, which moves nothing, whatever the verdict.
  it('marks a known species with the batch verdict, as a reagent decided', async () => {
    const decided = (verdict) => ({
      species: { kind: 'library', label: '[(CH4N2O)3+H]+', ion_formula: 'C3H13N6O3+' },
      verdict,
      files: 6,
      present: verdict === 'unpooled' ? 1 : 5,
      test_held: 1
    })
    const cells = await formulaCells(
      [
        { ...PARENT, known_species: decided('present') },
        { ...OTHER, known_species: decided('unpooled') },
        LINE
      ],
      'acrossRank'
    )
    const present = cells['bp-p'].find('[data-testid="mark-reagent"]')
    expect(present.attributes('aria-label')).toBe('[(CH4N2O)3+H]+: present across the batch')
    expect(present.attributes('data-tone')).toBe('neutral')
    expect(present.find('.ph-drop').exists()).toBe(true)
    const unpooled = cells['bp-other'].find('[data-testid="mark-reagent"]')
    expect(unpooled.attributes('aria-label')).toBe(
      '[(CH4N2O)3+H]+: in too few samples to decide for the batch'
    )
    expect(unpooled.attributes('data-tone')).toBe('neutral')
    expect(cells['bp-x'].find('[data-testid="mark-reagent"]').exists()).toBe(false)
  })

  // The search's persistence admission marks the formula, and the tier cell
  // names what the batch's own rules hold the tier down on.
  it('marks a batch peak searched on persistence, and the reason that holds its tier', async () => {
    const admitted = {
      share: 0.9167,
      n_present: 11,
      n_samples: 12,
      split: 0.4583,
      formula: 'C10H16O4'
    }
    const held = [{ rule: 'persistence_only', detail: 'searched because ...', caps: true }]
    const lifted = [{ rule: 'second_line', detail: 'a line of ...', caps: false }]
    // A batch peak the search took on its height carries neither.
    const PLAIN = peak('bp-plain', 'candidate', 0.6, { mz: 320.2, consensus_formula: 'C13H20O6' })
    // One the samples' vote put below candidate: the rule stands, holding
    // nothing at candidate.
    const BELOW = peak('bp-below', 'below_assignability', 0.3, {
      mz: 340.2,
      consensus_formula: 'C14H22O6',
      persistence: admitted,
      tier_reasons: held
    })
    const peaks = [
      { ...PARENT, consensus_tier: 'candidate', persistence: admitted, tier_reasons: held },
      { ...OTHER, persistence: admitted, tier_reasons: lifted },
      PLAIN,
      BELOW,
      LINE
    ]
    const cells = await formulaCells(peaks, 'acrossRank', { tooltips: true })
    const mark = cells['bp-p'].find('[data-testid="mark-persistence"]')
    expect(mark.attributes('aria-label')).toBe(
      'Searched on persistence: in 11 of 12 spectra (92 %)'
    )
    expect(mark.attributes('data-tone')).toBe('holds-back')
    expect(mark.attributes('data-tooltip')).toMatch(/is held at candidate until/)
    const below = cells['bp-below'].find('[data-testid="mark-persistence"]')
    expect(below.attributes('data-tone')).toBe('holds-back')
    expect(below.attributes('data-tooltip')).toMatch(/would be held at candidate/)
    expect(below.attributes('data-tooltip')).not.toMatch(/is held at candidate/)
    // Something besides its peak saw the reading: the admission holds nothing back.
    const kept = cells['bp-other'].find('[data-testid="mark-persistence"]')
    expect(kept.exists()).toBe(true)
    expect(kept.attributes('data-tone')).toBe('neutral')
    expect(cells['bp-x'].find('[data-testid="mark-persistence"]').exists()).toBe(false)
    expect(cells['bp-plain'].find('[data-testid="mark-persistence"]').exists()).toBe(false)

    wrapper.unmount()
    const tiers = await formulaCells(peaks, 'tierRank', { tooltips: true })
    const reason = tiers['bp-p'].find('[data-testid="tier-reason-mark"]')
    expect(reason.attributes('aria-label')).toBe('persistence only')
    expect(reason.attributes('data-tooltip')).toMatch(/^Holds this row at candidate/)
    // The arrow still names the reason below candidate, as a run's does, and
    // says the evidence already put the batch peak lower.
    expect(
      tiers['bp-below'].find('[data-testid="tier-reason-mark"]').attributes('data-tooltip')
    ).toMatch(/^Would hold this row at candidate, but its evidence already puts it lower/)
    expect(tiers['bp-other'].find('[data-testid="tier-reason-mark"]').exists()).toBe(false)
    expect(tiers['bp-x'].find('[data-testid="tier-reason-mark"]').exists()).toBe(false)
    expect(tiers['bp-plain'].find('[data-testid="tier-reason-mark"]').exists()).toBe(false)
  })

  // The water ladder's mark sits beside them in the same cell.
  it('marks a rung of a water ladder as a reagent decided, claimed or not', async () => {
    const rung = (role) => ({
      rung: { core: '[Br]-', ion: '[Br+16xH2O]-', track: 'M0', waters: 16 },
      segments: [
        { segment: 0, spectra: 12, presence: 0.9, decoy_presence: 0.05, verdict: 'passed' }
      ],
      passed_in: [0],
      reads: { role }
    })
    const cells = await formulaCells(
      [{ ...PARENT, water_ladder: rung('reagent') }, { ...OTHER, water_ladder: rung(null) }, LINE],
      'acrossRank'
    )
    expect(cells['bp-x'].find('[data-testid="mark-reagent"]').exists()).toBe(false)
    const claimed = cells['bp-p'].find('[data-testid="mark-reagent"]')
    expect(claimed.attributes('aria-label')).toBe(
      '[Br+16xH2O]-: a water-ladder rung in 1 of 1 stretch of acquisition'
    )
    expect(claimed.attributes('data-tone')).toBe('neutral')
    const kept = cells['bp-other'].find('[data-testid="mark-reagent"]')
    expect(kept.attributes('data-tone')).toBe('neutral')
  })

  // A line is a sub-row and repeats nothing: its rung's row carries the badge.
  it('marks no heavier line of a rung folded under it', async () => {
    const line = {
      rung: { core: '[Br]-', ion: '[Br+16xH2O]-', track: '81Br', waters: 16 },
      segments: [],
      passed_in: [0],
      reads: { role: 'reagent' }
    }
    const cells = await formulaCells([PARENT, { ...LINE, water_ladder: line }, OTHER])
    expect(cells['bp-x'].find('.child-label').exists()).toBe(true)
    expect(cells['bp-x'].find('[data-testid="mark-reagent"]').exists()).toBe(false)
    wrapper.unmount()
    const badges = await formulaCells(
      [PARENT, { ...LINE, water_ladder: line }, OTHER],
      'acrossRank'
    )
    expect(badges['bp-x'].find('[data-testid="mark-reagent"]').exists()).toBe(false)
  })
})

describe('PaneBrowserBatchPeaks ratio stability, across the batch', () => {
  // A pair entry as the continuity pass records it on an anchor.
  const pair = (grade, extra = {}) => ({
    kind: 'adduct',
    partner_batch_peak_id: 'bp-partner',
    partner_mz: 262.0569,
    grade,
    reason: null,
    n: 41,
    n_times: 41,
    ratio_median: 0.42,
    ratio_spread: 0.08,
    sigma_log: 0.077,
    rho: 0.12,
    drift_log: 0.05,
    drift_fold: 1.05,
    co_variation: null,
    ...extra
  })
  // An anchor's detail, graded for its own claim at its own prevalence.
  const detail = (grades, extra = {}) => ({
    v: 1,
    eligible: true,
    reason: null,
    formula: 'C6H12O6',
    ionization_mechanism_id: null,
    n_present_at_compute: 2,
    n_pairs: grades.length,
    grades: Object.fromEntries(
      [...new Set(grades)].map((grade) => [grade, grades.filter((g) => g === grade).length])
    ),
    warnings: [],
    pairs: grades.map((grade) => pair(grade)),
    ...extra
  })
  const graded = (id, grade, mz, extra = {}) =>
    peak(id, 'assigned', 0.9, {
      mz,
      continuity_grade: grade,
      continuity: detail([grade]),
      continuity_stale: false,
      ...extra
    })
  const peaks = () => [
    graded('bp-stable', 'stable', 100.1),
    graded('bp-drift', 'drift', 110.1),
    graded('bp-red', 'red', 120.1),
    graded('bp-inconclusive', 'inconclusive', 130.1),
    graded('bp-insufficient', 'insufficient', 140.1),
    graded('bp-partial', 'stable', 150.1, {
      continuity: detail(['stable'], { warnings: ['partial'], n_pairs: 9 })
    }),
    // A sample folded in since: graded at 2, present in 3 now.
    graded('bp-stale', 'stable', 160.1, { n_present: 3, continuity_stale: true }),
    // A batch that is not time-ordered: no grade, and the reason.
    peak('bp-withdrawn', 'assigned', 0.9, {
      mz: 170.1,
      continuity_grade: null,
      continuity: detail([], { eligible: false, reason: 'gap', n_pairs: 1 })
    }),
    peak('bp-none', 'assigned', 0.9, { mz: 180.1 }),
    // Every pair it is in fell past the per-batch cap: no grade, but partial.
    peak('bp-ungraded', 'assigned', 0.9, {
      mz: 185.1,
      continuity_grade: null,
      continuity: detail([], { warnings: ['partial'], n_pairs: 3 }),
      continuity_stale: false
    }),
    // The main peak of a family and its isotope line, graded on their pair.
    graded('bp-m0', 'stable', 190.1),
    graded('bp-m1', 'red', 191.1, {
      isotopologue_of: 'bp-m0',
      continuity: detail(['red'], {
        pairs: [pair('red', { kind: 'isotopologue', partner_batch_peak_id: 'bp-m0' })]
      })
    }),
    // An isotope line the pass left nothing on: an empty cell, not a dash.
    peak('bp-m2', 'assigned', 0.9, { mz: 192.1, isotopologue_of: 'bp-m0' })
  ]
  const continuityCell = (cells, id) => cells[id].find('[data-testid="continuity"]')

  // The grade is the registry's badge in Across the batch, in its tone, and a
  // button that opens the pairs behind it. A line repeats none of its
  // family's: the M0's pairs hold the line's.
  it('draws a graded anchor as a badge in its tone, and a line none', async () => {
    const cells = await cellsOf('acrossRank', peaks())
    const badge = (id) => cells[id]?.find('[data-testid="mark-ratio"]')
    expect(badge('bp-stable').attributes('data-tone')).toBe('supports')
    expect(badge('bp-drift').attributes('data-tone')).toBe('drifted')
    expect(badge('bp-red').attributes('data-tone')).toBe('holds-back')
    expect(badge('bp-inconclusive').attributes('data-tone')).toBe('neutral')
    expect(badge('bp-none').exists()).toBe(false)
    expect(continuityCell(cells, 'bp-red').attributes('aria-label')).toBe(
      'Ratio stability: open the pairs behind the grade'
    )
    // Folded, the lines are not drawn; unfolded, they carry no badge.
    expect(cells['bp-m1']).toBeUndefined()
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    const unfolded = Object.fromEntries(
      wrapper
        .findAll('.column-stub')
        .find((col) => col.attributes('data-field') === 'acrossRank')
        .findAll('.stub-cell')
        .map((cell) => [cell.attributes('data-row'), cell])
    )
    expect(unfolded['bp-m1'].find('[data-testid="mark-ratio"]').exists()).toBe(false)
    expect(unfolded['bp-m0'].find('[data-testid="mark-ratio"]').exists()).toBe(true)
  })

  it('opens the pairs of the row whose cell was clicked', async () => {
    const cells = await cellsOf('acrossRank', peaks())
    await continuityCell(cells, 'bp-red').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.continuityTarget.batch_peak_id).toBe('bp-red')
    const popover = wrapper.findComponent({ name: 'BatchPeakContinuityPopover' })
    expect(popover.props('row').batch_peak_id).toBe('bp-red')
    expect(popover.props('isotopeLine')).toBe(false)
  })

  it('closes the verdict popover when a continuity cell opens, and the other way round', async () => {
    const cells = await cellsOf('acrossRank', peaks())
    const verdictCell = () =>
      wrapper
        .findAll('.column-stub')
        .find((col) => col.attributes('data-field') === 'verdictRank')
        .findAll('.stub-cell')
        .find((c) => c.attributes('data-row') === 'bp-red')
        .find('.verdict-cell')
    popoverCalls.length = 0
    await continuityCell(cells, 'bp-red').trigger('click')
    expect(popoverCalls).toContainEqual(['Batch-level verdict', 'hide'])
    popoverCalls.length = 0
    await verdictCell().trigger('click')
    expect(popoverCalls).toContainEqual(['Ratio stability', 'hide'])
  })

  // A batch peak nobody has judged draws no mark in its verdict cell: the cell
  // is still the button that records one.
  it('draws nothing in an unjudged verdict cell, and keeps it the button that judges', async () => {
    await cellsOf('acrossRank', peaks())
    const cell = wrapper
      .findAll('.column-stub')
      .find((col) => col.attributes('data-field') === 'verdictRank')
      .findAll('.stub-cell')
      .find((c) => c.attributes('data-row') === 'bp-red')
      .find('.verdict-cell')
    expect(cell.attributes('aria-label')).toBe('Record a batch-level verdict')
    expect(cell.classes()).toContain('unjudged')
    expect(cell.find('.pi').exists()).toBe(false)
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

  // The search box takes the Formula column's filter menu's place.
  const search = (query) => wrapper.find('[data-testid="ledger-search"]').setValue(query)

  // The store keeps a stored sort only on a column it lists for the scope, so
  // every sortable column, the optional ones shown, must be on that list.
  it("lists every sortable column among the store's sort fields", async () => {
    wrapper = await mountPane({
      peaks: [
        peak('bp-all', 'assigned', 0.9, {
          engine_tier: 'assigned',
          member_level: LEVELS[0],
          engine_level: LEVELS[0],
          co_variation: { verdict: 'co_varying', r: 0.8, n_spectra: 20 },
          batch_checks: { halogen_lock: { element: 'Br', verdict: 'locked' } },
          continuity: { pairs: [] }
        })
      ]
    })

    const sorted = wrapper
      .findAllComponents(ColumnStub)
      .filter((column) => column.props('sortable'))
      .map((column) => column.props('sortField') ?? column.props('field'))
    for (const field of ['consensus_ion_formula', 'memberLevelRank', 'n_present', 'acrossRank']) {
      expect(sorted).toContain(field)
    }
    // The engine's readings are chips, and the batch's readings badges: no
    // column of their own.
    for (const field of ['engineTierRank', 'engineLevelRank', 'continuityRank', 'listingName']) {
      expect(sorted).not.toContain(field)
    }
    expect(SORT_FIELDS.batch).toEqual(expect.arrayContaining(sorted))
  })

  it('has no filter menu on the Formula column any more', async () => {
    wrapper = await mountPane({ peaks })

    expect(Object.keys(wrapper.vm.filters)).not.toContain('consensus_formula')
  })

  it('narrows the rows on a formula typed in the search', async () => {
    wrapper = await mountPane({ peaks })

    await search('c6h')

    expect(ids()).toEqual(['bp-1', 'bp-3'])
  })

  it('narrows the rows on a listed name', async () => {
    wrapper = await mountPane({
      peaks: [
        ...peaks,
        peak('bp-listed', 'assigned', 0.6, {
          consensus_formula: 'C10H30O5Si5',
          reference_listing: {
            name: 'decamethylcyclopentasiloxane',
            source: 'cyclic-siloxanes',
            tags: [],
            total: 1
          }
        })
      ]
    })

    await search('cyclopenta')

    expect(ids()).toEqual(['bp-listed'])
  })

  // A target alone, with no list's name: the target names the row.
  it("narrows the rows on a target's name", async () => {
    wrapper = await mountPane({
      peaks: [
        ...peaks,
        peak('bp-target', 'assigned', 0.6, {
          consensus_formula: 'C6H12O6',
          reference_listing: {
            name: null,
            source: null,
            inchikey: null,
            tags: [],
            total: 0,
            target: { label: 'target', name: 'glucose', inchikey: null, listed: false }
          }
        })
      ]
    })

    await search('gluc')

    expect(ids()).toEqual(['bp-target'])
  })

  // The batch's rows come from many runs, so an m/z matches within 5 ppm, the
  // window an imported batch result is matched at.
  it('matches an m/z within 5 ppm and not beyond', async () => {
    wrapper = await mountPane({
      peaks: [
        peak('bp-1', 'assigned', 0.9, { mz: 200.0 }),
        peak('bp-2', 'assigned', 0.8, { mz: 300.0 })
      ]
    })

    // 200 * 5e-6 = 0.001
    await search('200.0009')
    expect(ids()).toEqual(['bp-1'])
    await search('199.9991')
    expect(ids()).toEqual(['bp-1'])
    await search('200.0011')
    expect(ids()).toEqual([])
  })

  it("finds a family by its isotope line's m/z", async () => {
    wrapper = await mountPane({
      peaks: [
        peak('bp-m0', 'assigned', 0.9, { mz: 181.0707 }),
        peak('bp-iso', 'assigned', 0.9, { mz: 182.0741, isotopologue_of: 'bp-m0' }),
        peak('bp-other', 'candidate', 0.5, { mz: 250.1 })
      ]
    })

    await search('182.0741')

    expect(ids()).toEqual(['bp-m0'])
  })

  it('shows every row again when the search is cleared', async () => {
    wrapper = await mountPane({ peaks })
    await search('C6H')

    await search('')

    expect(ids()).toHaveLength(3)
  })

  it('keeps a family whole when its main peak passes the search', async () => {
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
    await search('C6H12O6')

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

    expect(wrapper.vm.intensityTooltip).toMatch(/^Max intensity: the brightest sample's/)
    expect(wrapper.vm.intensityTooltip).toMatch(/highest/i)
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

  // A line is a sub-row and carries no box (docs/dev/assignment_ledger_and_card.md,
  // section 3.3): its family's row plots it. A range or a select-all across
  // unfolded lines selects their families only.
  it('selects no isotopologue line, folded or not', async () => {
    wrapper = await mountPane({ peaks: [M0, ISO, OTHER] })
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.rows.map((row) => row.batch_peak_id)).toEqual(['bp-m0', 'bp-iso', 'bp-other'])

    table().vm.$emit('update:selection', wrapper.vm.rows)
    await wrapper.vm.$nextTick()
    expect(selectedIds()).toEqual(['bp-m0', 'bp-other'])

    // A click on the line alone selects nothing.
    table().vm.$emit('update:selection', [...app.data.batchPeak.selected, wrapper.vm.rows[1]])
    await wrapper.vm.$nextTick()
    expect(selectedIds()).toEqual(['bp-m0', 'bp-other'])

    // Select all: the families, not their lines; and the header box reads
    // checked once they are, the lines it cannot hold aside.
    table().vm.$emit('select-all-change', { checked: true })
    await wrapper.vm.$nextTick()
    expect(selectedIds()).toEqual(['bp-m0', 'bp-other'])
    expect(table().props('selectAll')).toBe(true)
  })

  // A line handed over is no row the cap refused: a click on one beside a
  // full selection is no click on a row of its own.
  it('reads a click on a line beside a full selection as no refusal', async () => {
    const families = Array.from({ length: MAX_SELECTED_BATCH_PEAKS }, (_, i) =>
      peak(`bp-${i}`, 'assigned', 0.9, { mz: 400 + i })
    )
    wrapper = await mountPane({ peaks: [M0, ISO, ...families] })
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    const full = wrapper.vm.rows.filter((row) => !row.parentId).slice(0, MAX_SELECTED_BATCH_PEAKS)
    table().vm.$emit('update:selection', full)
    await wrapper.vm.$nextTick()
    const line = wrapper.vm.rows.find((row) => row.batch_peak_id === 'bp-iso')
    table().vm.$emit('update:selection', [...full, line])
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.selectionNotice).toBeNull()
    expect(app.data.batchPeak.selected).toHaveLength(MAX_SELECTED_BATCH_PEAKS)
  })

  // A selection made elsewhere, or before the lines lost their box, can still
  // hold one: it leaves the selection.
  it('drops a line a selection still holds', async () => {
    wrapper = await mountPane({ peaks: [M0, ISO, OTHER] })
    app.data.batchPeak.selected = [served(M0), served(ISO)]
    await wrapper.vm.$nextTick()
    expect(selectedIds()).toEqual(['bp-m0'])
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

  it('is toggled from Show, at the end of the filter bar', async () => {
    // Where the sample ledger keeps it: behind Show at the end of the filter
    // bar, rather than on the panel header it used to share with the compute
    // button. The toggle is inside the popover Show opens.
    wrapper = await mountPane({ peaks: [M0, ISO] })

    const show = wrapper.find('.filter-bar .view-menu-button')
    expect(show.attributes('data-icon')).toBe('pi ph ph-funnel')
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

    // After Samples and Across the batch; the row's actions close the row.
    const columns = wrapper.findAllComponents(ColumnStub).map((column) => column.props('field'))
    expect(columns.at(-1)).toBeUndefined()
    expect(columns.at(-2)).toBe('verdictRank')
    expect(columns.at(-3)).toBe('acrossRank')
    expect(columns.at(-4)).toBe('n_present')
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
    wrapper.vm.openVerdict(new Event('click'), { batch_peak_id: 'bp-2' })
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.verdictTarget.batch_peak_id).toBe('bp-2')
    // The row as the ledger now holds it, not a copy taken when the cell was clicked.
    expect(wrapper.vm.verdictTarget.verdictRank).toBe(0)
  })

  it('hands the verdict cell its record, whether it is stale, and its words', async () => {
    const MOVED = peak('bp-5', 'assigned', 0.5, { consensus_formula: 'C7H8' })
    wrapper = await mountPane({
      peaks: [...PEAKS, MOVED],
      verdicts: { ...VERDICTS, 'bp-5': verdictOn('bp-5', 'confirmed') }
    })
    const row = (id) => wrapper.vm.rows.find((r) => r.batch_peak_id === id)

    expect(wrapper.vm.verdictCell(row('bp-2'))).toMatchObject({
      record: VERDICTS['bp-2'],
      stale: false,
      disabled: false,
      label: 'Batch-level verdict'
    })
    // Judged as C6H12O6, and the consensus has moved to C7H8.
    expect(wrapper.vm.verdictCell(row('bp-5')).stale).toBe(true)
    expect(wrapper.vm.verdictCell(row('bp-3'))).toMatchObject({
      record: null,
      label: 'Record a batch-level verdict'
    })
  })
})

describe('PaneBrowserBatchPeaks batch runs', () => {
  /** A batch's runs as the runs route lists them, newest first: one of each kind. */
  const HISTORY = [
    { batch_peak_run_id: 'r-7', action: 'assign', current: true },
    { batch_peak_run_id: 'r-6', action: 'assign', summary: { first_stage: 'series' } },
    { batch_peak_run_id: 'r-5', action: 'assign', summary: { first_stage: 'water_ladders' } },
    { batch_peak_run_id: 'r-4', action: 'search_untargeted' },
    { batch_peak_run_id: 'r-3', action: 'import' },
    { batch_peak_run_id: 'r-2', action: 'rebuild' },
    { batch_peak_run_id: 'r-1', action: 'fold' }
  ].map((run) => ({ current: false, status: 'completed', ...run }))

  /** Put a run on screen, as Run history does. */
  async function viewRun(run) {
    app.data.batchPeakRun.viewing = run
    app.data.batchPeakRun.viewingCurrent = Boolean(run.current)
    app.data.batchPeakRun.viewingId = run.current ? null : run.batch_peak_run_id
    await wrapper.vm.$nextTick()
  }

  /** Put an earlier run on screen, one the history does not list. */
  const viewEarlierRun = (action = 'search_untargeted') =>
    viewRun({ batch_peak_run_id: 'r-old', action, current: false })

  it('says nothing about history while the live ledger is on screen', async () => {
    wrapper = await mountPane({ peaks: [peak('bp-1', 'assigned', 0.9)] })
    expect(wrapper.findAll('.pane-message')).toHaveLength(0)
  })

  it('names the run whose snapshot is on screen, and that it is read-only', async () => {
    wrapper = await mountPane({ peaks: [peak('bp-1', 'assigned', 0.9)] })
    app.data.batchPeakRun.list = HISTORY
    await viewRun(HISTORY[1])

    const notice = wrapper.findAll('.pane-message').map((message) => message.text())
    expect(notice).toHaveLength(1)
    expect(notice[0]).toContain(
      'Showing the ledger as run #6, Assignment from a stage (Detect series), left it, read-only.'
    )
    expect(notice[0]).toContain('Back to current, in the bar above')
  })

  // The banner and Run history name a run alike: the same number, the same label.
  it("names each kind of run by Run history's number and label", async () => {
    wrapper = await mountPane({ peaks: [peak('bp-1', 'assigned', 0.9)] })
    app.data.batchPeakRun.list = HISTORY
    const history = mount(BatchRunHistory, {
      global: { stubs: GLOBAL_STUBS, directives: { tooltip: {}, help: {} } }
    })
    await history.vm.$nextTick()
    const rowOf = (run) => {
      const row = history.find(`[data-run="${run.batch_peak_run_id}"]`)
      return {
        ordinal: row.find('.row-ordinal').text(),
        label: row.find('[data-testid="run-label"]').text()
      }
    }

    const seen = []
    for (const run of HISTORY.slice(1)) {
      await viewRun(run)
      const { ordinal, label } = rowOf(run)
      const notice = wrapper.find('.pane-message').text()
      expect(notice).toContain(
        `Showing the ledger as run ${ordinal}, ${label}, left it, read-only.`
      )
      seen.push(`${ordinal} ${label}`)
    }
    // What those rows say, so the comparison above is not of two empty strings.
    expect(seen).toEqual([
      '#6 Assignment from a stage (Detect series)',
      '#5 Assignment',
      '#4 Search untargeted',
      '#3 Import',
      '#2 Rebuild',
      '#1 Folded samples'
    ])
    history.unmount()
  })

  it('names a run the history no longer lists by its label alone', async () => {
    wrapper = await mountPane({ peaks: [peak('bp-1', 'assigned', 0.9)] })
    app.data.batchPeakRun.list = HISTORY
    await viewRun({ batch_peak_run_id: 'r-pruned', action: 'rebuild', current: false })

    const notice = wrapper.find('.pane-message').text()
    expect(notice).toContain('Showing the ledger as Rebuild left it, read-only.')
    expect(notice).not.toContain('#')
  })

  it('names an action Run history does not know as Run history gives it', async () => {
    wrapper = await mountPane({ peaks: [peak('bp-1', 'assigned', 0.9)] })
    const run = { batch_peak_run_id: 'r-x', action: 'unknown_action', current: false }
    app.data.batchPeakRun.list = [HISTORY[0], run]
    await viewRun(run)

    expect(runLabel(run)).toBe('unknown_action')
    expect(wrapper.find('.pane-message').text()).toContain(
      'Showing the ledger as run #1, unknown_action, left it, read-only.'
    )
  })

  // History is read-only: the cells keep their badges and lose their hand.
  it('withholds the verdict cells while an earlier run is on screen', async () => {
    wrapper = await mountPane({ peaks: [peak('bp-1', 'assigned', 0.9)] })
    const row = wrapper.vm.rows[0]
    expect(wrapper.vm.verdictCell(row).disabled).toBe(false)
    await viewEarlierRun()
    expect(wrapper.vm.verdictCell(row).disabled).toBe(true)
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
  it('offers the export from the overflow menu and launches it', async () => {
    wrapper = await mountPane({ peaks: [peak('bp-1', 'assigned', 0.9)] })

    // The overflow's panel, not Show's.
    const overflow = wrapper.find('#batch-peak-overflow-menu')
    expect(wrapper.find('#batch-peak-view-menu .export-ledger').exists()).toBe(false)
    const button = overflow.find('.export-ledger')
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
    expect(useBatchAssign().exporting).toBe(true)
    notificationHandlers.export_batch_ledger?.forEach((cb) =>
      cb({ status: 'success', process_id: 'proc-1' })
    )
    expect(useBatchAssign().exporting).toBe(false)
  })

  it('offers no export on an empty ledger', async () => {
    // The overflow sits in the filter bar, which an empty ledger does not
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

  // Owner's decision of 2026-11-22: a second, outlined chip in the Tier cell
  // behind Show > Engine readings, not a column of its own.
  it("shows the engine's tier as an outlined chip in the Tier cell, the consensus left as it is", async () => {
    await cellsOf('tierRank', peaks())
    const tierCell = (id) =>
      wrapper
        .findAll('.column-stub')
        .find((col) => col.attributes('data-field') === 'tierRank')
        .find(`.stub-cell[data-row="${id}"]`)
    expect(columnFor('engineTierRank')).toBeUndefined()
    expect(tierCell('bp-1').findAll('.tier-tag')).toHaveLength(1)

    wrapper.vm.engineView = true
    await wrapper.vm.$nextTick()
    const chips = tierCell('bp-1').findAll('.tier-tag')
    expect(chips.map((chip) => chip.attributes('data-tier'))).toEqual(['assigned', 'candidate'])
    expect(chips[1].attributes('data-outlined')).toBe('true')
    expect(chips[1].attributes('data-tooltip')).toContain("The importing engine's own tier")
    // A formula the engine stated no tier for: its own chip alone.
    expect(tierCell('bp-3').findAll('.tier-tag')).toHaveLength(1)
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

  it('names both tiers in words, not as the API keys', async () => {
    wrapper = await mountPane({ peaks: peaks() })
    const text = wrapper.vm.engineTierTooltip({
      consensus_tier: 'candidate',
      engine_tier: 'below_assignability'
    })
    const reverse = wrapper.vm.engineTierTooltip({
      consensus_tier: 'below_assignability',
      engine_tier: 'candidate'
    })
    // The engine's tier where it agrees, with a name that is not its key.
    const agrees = wrapper.vm.engineTierTooltip({
      consensus_tier: 'below_assignability',
      engine_tier: 'below_assignability'
    })

    expect(text).toContain("The importing engine's own tier: below assignability, where")
    expect(reverse).toContain("the members' measurements says below assignability")
    expect(agrees).toContain('own tier: below assignability (agrees with the consensus)')
    expect(`${text}${reverse}${agrees}`).not.toContain('below_assignability')
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
    expect(chips()).toEqual(['2 assigned', '1 candidate', '1 unassigned'])
    expect(wrapper.find('.engine-view-label').exists()).toBe(false)

    await toggle('batch-engine-tier-view').trigger('click')

    expect(chips()).toEqual(['1 assigned', '1 candidate', '1 unassigned', '1 not stated'])
    expect(wrapper.find('.engine-view-label').exists()).toBe(true)
    wrapper.vm.toggleTier('candidate')
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-1'])
    // Multi-select: a second chip adds its bucket.
    wrapper.vm.toggleTier('engine_unstated')
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-1', 'bp-3'])
    wrapper.vm.toggleTier('candidate')
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-3'])
  })

  // A chip names a bucket of one view: in the engine-tier view the engine's,
  // while the tier column still shows the consensus. Changing views drops the
  // chips rather than carrying a bucket over to a tier it never meant.
  it("drops the engine view's chips when the view changes", async () => {
    wrapper = await mountPane({ peaks: peaks() })
    await toggle('batch-engine-tier-view').trigger('click')

    // bp-1 is the engine's candidate, though the consensus says assigned.
    wrapper.vm.toggleTier('candidate')
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-1'])
    expect(wrapper.find('.tier-stat.candidate').classes()).toContain('active')
    expect(wrapper.find('.tier-stat.assigned').classes()).not.toContain('active')

    // Leaving the view drops it, and coming back brings it not back.
    await toggle('batch-engine-tier-view').trigger('click')
    expect(ids()).toHaveLength(4)
    expect(wrapper.vm.activeTiers.size).toBe(0)
    await toggle('batch-engine-tier-view').trigger('click')
    expect(ids()).toHaveLength(4)
  })

  // Both switches outlive the ledger they were flipped on; one no engine tiered
  // has nothing to read in its words, and no switch left to turn them off.
  it('reads the consensus again on a ledger no engine tiered, whatever the switches', async () => {
    wrapper = await mountPane({ peaks: peaks() })
    await toggle('batch-engine-tier-view').trigger('click')
    await toggle('batch-engine-disagrees').trigger('click')

    app.data.batchPeak.list = peaks().map(({ engine_tier, ...bp }) => served(bp))
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

// The identification level the members' runs stated for the consensus
// formula, in a column of its own beside the tier, and the importing engine's
// beside it as its tier is. The batch states no level of its own, and the
// chip's hover says so.
describe('PaneBrowserBatchPeaks identification level', () => {
  const peaks = () => [
    peak('bp-1', 'assigned', 0.9, {
      mz: 100.1,
      member_level: '4a',
      member_levels: { '4a': 3, '5a': 2 },
      engine_level: '4a'
    }),
    peak('bp-2', 'candidate', 0.5, {
      mz: 200.1,
      member_level: '5a',
      member_levels: { '5a': 2 },
      engine_level: '4b'
    }),
    // A batch peak from a ledger built before the level, or a snapshot's.
    peak('bp-3', 'assigned', 0.8, { mz: 300.1, member_level: null, member_levels: null }),
    peak('bp-4', 'candidate', 0.4, {
      mz: 400.1,
      member_level: '5b',
      member_levels: { '5b': 2 }
    })
  ]
  const withoutLevels = () =>
    peaks().map(({ member_level, member_levels, engine_level, ...bp }) => bp)
  const ids = () => wrapper.vm.rows.map((row) => row.batch_peak_id)

  it('is absent from a ledger whose members stated no level', async () => {
    wrapper = await mountPane({ peaks: withoutLevels() })

    expect(wrapper.vm.hasMemberLevels).toBe(false)
    expect(columnFor('member_level')).toBeUndefined()
    expect(columnFor('engineLevelRank')).toBeUndefined()
  })

  it("appears where any member level is shown, sorted on the scale's rank, one filter rule", async () => {
    wrapper = await mountPane({ peaks: peaks() })
    const column = columnFor('member_level')

    expect(column).toBeDefined()
    expect(column.props('sortField')).toBe('memberLevelRank')
    expect(column.props('sortable')).toBe(true)
    expect(column.props('maxConstraints')).toBe(1)
    expect(column.props('showAddButton')).toBe(false)
    expect(Object.keys(wrapper.vm.filters)).toContain('member_level')
  })

  it("shows the members' level, how they split, and that it is not the batch's own", async () => {
    const cells = await cellsOf('member_level', peaks())

    const chip = cells['bp-1'].find('.level-tag')
    expect(chip.attributes('data-level')).toBe('4a')
    const tooltip = chip.attributes('data-tooltip')
    expect(tooltip).toContain("Stated by the members' runs: 3 members at 4a, 2 at 5a")
    expect(tooltip).toContain('not one the batch states of its own')
    expect(cells['bp-3'].find('.level-tag').exists()).toBe(false)
    expect(cells['bp-3'].text()).toBe('')
  })

  it('ranks each batch peak by the scale, one with no level null', async () => {
    wrapper = await mountPane({ peaks: peaks() })
    const byId = Object.fromEntries(wrapper.vm.rows.map((row) => [row.batch_peak_id, row]))

    expect(byId['bp-1'].memberLevelRank).toBe(LEVELS.indexOf('4a'))
    expect(byId['bp-4'].memberLevelRank).toBe(LEVELS.indexOf('5b'))
    expect(byId['bp-3'].memberLevelRank).toBeNull()
  })

  it('sorts on the scale, a batch peak with no level last in both directions', async () => {
    wrapper = await mountPane({ peaks: peaks() })

    wrapper.vm.sortField = 'memberLevelRank'
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-1', 'bp-2', 'bp-4', 'bp-3'])

    wrapper.vm.sortOrder = -1
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-4', 'bp-2', 'bp-1', 'bp-3'])
  })

  it("narrows the rows to the members' level its menu picks", async () => {
    wrapper = await mountPane({ peaks: peaks() })

    wrapper.vm.filters.member_level.constraints[0].value = '5a'
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-2'])

    wrapper.vm.filters.member_level.constraints[0].value = null
    await wrapper.vm.$nextTick()
    expect(ids()).toHaveLength(4)
  })

  // The batch-level verdict on the row's present claim states a level: the
  // column reads the claim's in place of the members' (docs/dev/
  // identification_claims.md, section 6.1).
  describe("a live batch-level claim's level", () => {
    const claimOn = (batch_peak_id, extra = {}) => ({
      batch_peak_verification_id: `v-${batch_peak_id}`,
      batch_peak_id,
      assigned_formula: 'C6H12O6',
      ionization_mechanism_id: null,
      verdict: 'confirmed',
      evidence_level: 'reference_standard',
      level: 1,
      level_letter: null,
      window_start_utc: '2026-01-01T00:00:00Z',
      window_end_utc: '2026-01-02T00:00:00Z',
      context: { claim: { evidence: 'a standard and an MS/MS line', lift: null } },
      superseded_utc: null,
      ...extra
    })

    it("shows the claim's level, says so, and what the members stated", async () => {
      const cells = await cellsOf('member_level', peaks(), { 'bp-2': claimOn('bp-2') })

      const chip = cells['bp-2'].find('.level-tag')
      expect(chip.attributes('data-level')).toBe('1')
      const tooltip = chip.attributes('data-tooltip').split('\n')
      expect(tooltip).toContain('Source: claim - the live verdict states this level')
      expect(tooltip).toContain('Why: a standard and an MS/MS line')
      expect(tooltip).toContain("The members' runs stated 5a")
      // A row no claim reaches reads the members' level, and says whose.
      expect(cells['bp-1'].find('.level-tag').attributes('data-level')).toBe('4a')
      expect(cells['bp-1'].find('.level-tag').attributes('data-tooltip')).toContain(
        'Source: engine - no live batch-level verdict states a level'
      )
    })

    it("ranks, sorts and filters on the claim's level", async () => {
      wrapper = await mountPane({ peaks: peaks(), verdicts: { 'bp-4': claimOn('bp-4') } })
      const byId = Object.fromEntries(wrapper.vm.rows.map((row) => [row.batch_peak_id, row]))
      expect(byId['bp-4'].memberLevelRank).toBe(LEVELS.indexOf('1'))

      wrapper.vm.sortField = 'memberLevelRank'
      wrapper.vm.sortOrder = 1
      await wrapper.vm.$nextTick()
      expect(ids()).toEqual(['bp-4', 'bp-1', 'bp-2', 'bp-3'])

      wrapper.vm.filters.member_level.constraints[0].value = '1'
      await wrapper.vm.$nextTick()
      expect(ids()).toEqual(['bp-4'])
    })

    it('offers the column where only a claim states a level', async () => {
      wrapper = await mountPane({
        peaks: withoutLevels(),
        verdicts: { 'bp-3': claimOn('bp-3', { level: 2, level_letter: 'a' }) }
      })

      expect(wrapper.vm.hasMemberLevels).toBe(true)
      expect(columnFor('member_level')).toBeDefined()
    })

    // A claim recorded under version 1 of the mapping has a number and no letter.
    it("shows a version-1 claim's bare number, and offers it in the menu", async () => {
      wrapper = await mountPane({
        peaks: peaks(),
        verdicts: { 'bp-1': claimOn('bp-1', { level: 3, level_letter: null }) }
      })
      const byId = Object.fromEntries(wrapper.vm.rows.map((row) => [row.batch_peak_id, row]))
      expect(byId['bp-1'].shownLevel.level).toBe('3')

      const options = wrapper
        .findComponent({ name: 'AssignmentLedger' })
        .props('levelOptions')
        .map((option) => option.value)
      expect(options).toEqual([...LEVELS.slice(0, 3), '3', ...LEVELS.slice(3)])
    })

    // A stale verdict is about a formula the consensus has left.
    it('reads no level off a stale verdict', async () => {
      const cells = await cellsOf('member_level', peaks(), {
        'bp-1': claimOn('bp-1', { assigned_formula: 'C7H14O7' })
      })

      expect(cells['bp-1'].find('.level-tag').attributes('data-level')).toBe('4a')
    })

    it("reads the members' level off a verdict that states none", async () => {
      const cells = await cellsOf('member_level', peaks(), {
        'bp-1': claimOn('bp-1', { verdict: 'rejected', level: null })
      })

      expect(cells['bp-1'].find('.level-tag').attributes('data-level')).toBe('4a')
    })
  })

  // Left on with no column to show it, the filter would empty the table for a
  // reason nothing on screen says.
  it('filters nothing where no batch peak shown carries a level', async () => {
    wrapper = await mountPane({ peaks: withoutLevels() })

    wrapper.vm.filters.member_level.constraints[0].value = '4a'
    await wrapper.vm.$nextTick()
    expect(ids()).toHaveLength(4)
  })

  // As the engine's tier is: an outlined chip in the Level cell, behind Show >
  // Engine readings.
  it("shows the importing engine's level as an outlined chip beside the members'", async () => {
    await cellsOf('member_level', peaks())
    const levelCell = (id) =>
      wrapper
        .findAll('.column-stub')
        .find((col) => col.attributes('data-field') === 'member_level')
        .find(`.stub-cell[data-row="${id}"]`)
    expect(columnFor('engineLevelRank')).toBeUndefined()
    expect(levelCell('bp-1').find('[data-testid="engine-level-chip"]').exists()).toBe(false)

    wrapper.vm.engineView = true
    await wrapper.vm.$nextTick()
    const chip = (id) => levelCell(id).find('[data-testid="engine-level-chip"]')
    expect(chip('bp-1').attributes('data-level')).toBe('4a')
    expect(chip('bp-1').attributes('data-outlined')).toBe('true')
    expect(chip('bp-2').attributes('data-level')).toBe('4b')
    expect(chip('bp-4').exists()).toBe(false)
  })

  // Show offers its engine readings where the batch carries any, a level alone
  // included, and the Level column opens for an engine level the members do not
  // state.
  it("offers the engine's readings, and the Level column, for an engine level alone", async () => {
    wrapper = await mountPane({
      peaks: peaks().map(({ engine_tier, member_level, ...bp }) => bp)
    })
    expect(wrapper.vm.hasEngineReadings).toBe(true)
    expect(columnFor('member_level')).toBeUndefined()
    wrapper.vm.engineView = true
    await wrapper.vm.$nextTick()
    expect(columnFor('member_level')).toBeDefined()
    // No engine tier: nothing to disagree with.
    expect(
      wrapper
        .findAllComponents({ name: 'ToggleSwitch' })
        .find((component) => component.props('inputId') === 'batch-engine-disagrees')
    ).toBeUndefined()
  })

  it("names the engine's level, and whether the members' runs agree", async () => {
    wrapper = await mountPane({ peaks: peaks() })
    const [agrees, differs, none] = [peaks()[0], peaks()[1], { ...peaks()[2], engine_level: '5a' }]

    expect(wrapper.vm.engineLevelTooltip(agrees)).toContain(
      "It agrees with the level the members' runs stated"
    )
    const text = wrapper.vm.engineLevelTooltip(differs)
    expect(text).toContain("The importing engine's own level: 4b")
    expect(text).toContain("The members' runs stated 5a")
    expect(wrapper.vm.engineLevelTooltip(none)).toContain(
      "The members' runs stated no level for it"
    )
  })

  it("shows the engine's two lines about its level as it wrote them", async () => {
    wrapper = await mountPane({ peaks: peaks() })
    const said = { ...peaks()[1], engine_evidence: 'its why', engine_lift: 'its lift' }

    const text = wrapper.vm.engineLevelTooltip(said)

    expect(text).toContain('Why: its why')
    expect(text).toContain('Would lift it: its lift')
    expect(wrapper.vm.engineLevelTooltip(peaks()[1])).not.toContain('Why:')
  })

  it('explains both headers on hover, and opens the level help on Level', async () => {
    app = makeApp({ peaks: peaks() })
    localStorage.clear()
    setActivePinia(createPinia())
    const record = (name) => (el, binding) =>
      el.setAttribute(name, (name === 'data-help' ? binding.value?.helpKey : binding.value) ?? '')
    wrapper = mount(PaneBrowserBatchPeaks, {
      global: {
        stubs: {
          ...GLOBAL_STUBS,
          Column: {
            ...ColumnStub,
            template: '<div class="column-stub"><slot name="header" /></div>'
          }
        },
        directives: {
          tooltip: { mounted: record('data-tooltip') },
          help: { mounted: record('data-help') }
        }
      }
    })
    await wrapper.vm.$nextTick()

    const headers = Object.fromEntries(
      wrapper.findAll('.column-stub [data-tooltip]').map((span) => [span.text(), span])
    )
    expect(headers.Level.attributes('data-tooltip')).toMatch(/^Identification level/)
    expect(headers.Level.attributes('data-tooltip')).toContain("members' runs")
    expect(headers.Level.attributes('data-tooltip')).toContain('no tier moves on it')
    expect(headers.Level.attributes('data-help')).toBe('assignment-level')
    expect(headers['Engine level']).toBeUndefined()
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
    // The other type's candidate is not counted: its chip is not drawn.
    expect(wrapper.vm.tierChips.find((chip) => chip.key === 'candidate')).toBeUndefined()
    expect(wrapper.vm.count).toBe('1 batch peak')
    // The other type's row is not an isotopologue folded out of sight.
    expect(wrapper.vm.countTooltip).toBeNull()
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

// The filter bar's Show menu and what it keeps: each choice filters as the
// cog's did, the new ones (roles, one badge kind, the verdict) included, and
// every choice outlives the pane - a visit to a sample unmounts it - and a
// reload.
describe('PaneBrowserBatchPeaks Show and its persistence', () => {
  const peaks = () => [
    peak('bp-1', 'assigned', 0.9, { mz: 100.1, curated: true }),
    peak('bp-2', 'candidate', 0.6, { mz: 200.1, series: { unit: 'CH2' } }),
    peak('bp-3', 'unassigned', null, { mz: 300.1, consensus_formula: null }),
    peak('bp-br', 'unassigned', null, {
      mz: 78.9189,
      consensus_formula: null,
      consensus_role: 'reagent',
      consensus_ion_formula: 'Br-'
    })
  ]
  const ids = () => wrapper.vm.rows.map((row) => row.batch_peak_id).sort()
  const badgeChips = () => wrapper.findAll('#batch-peak-view-menu .badge-filter .menu-chip')

  it('hides the role rows, and their chips, from Show > Roles', async () => {
    wrapper = await mountPane({ peaks: peaks() })
    expect(ids()).toEqual(['bp-1', 'bp-2', 'bp-3', 'bp-br'])

    await wrapper.find('#batch-show-roles').trigger('click')

    expect(ids()).toEqual(['bp-1', 'bp-2', 'bp-3'])
    expect(wrapper.find('.tier-stat.reagent').exists()).toBe(false)
  })

  it('keeps the rows carrying one badge kind, one at a time', async () => {
    wrapper = await mountPane({ peaks: peaks() })

    // Offered for the kinds some row carries, by the registry's names.
    expect(badgeChips().map((chip) => chip.text())).toEqual([
      'Homologous series',
      'Changed by hand'
    ])
    await wrapper.find('.badge-filter [data-value="manual"]').trigger('click')
    expect(ids()).toEqual(['bp-1'])
    await wrapper.find('.badge-filter [data-value="series"]').trigger('click')
    expect(ids()).toEqual(['bp-2'])
    // Clicked again, it is off.
    await wrapper.find('.badge-filter [data-value="series"]').trigger('click')
    expect(wrapper.vm.badgeFilter).toBeNull()
    expect(ids()).toHaveLength(4)
  })

  // A kind held from another batch, which no row here carries, is no filter:
  // it would empty the table under a choice the menu does not offer.
  it('filters nothing by a held badge kind no row carries', async () => {
    wrapper = await mountPane({ peaks: peaks() })
    wrapper.vm.badgeFilter = 'persistence'
    await wrapper.vm.$nextTick()
    expect(ids()).toHaveLength(4)
  })

  it('offers no badge filter where no row carries a badge', async () => {
    wrapper = await mountPane({ peaks: [peak('bp-1', 'assigned', 0.9)] })
    expect(wrapper.find('.badge-filter').exists()).toBe(false)
  })

  it('filters by the batch-level verdict from Show', async () => {
    wrapper = await mountPane({
      peaks: peaks(),
      verdicts: { 'bp-1': { verdict: 'confirmed', assigned_formula: 'C6H12O6' } }
    })
    const chip = (value) =>
      wrapper.find(`#batch-peak-view-menu .verdict-filter [data-value="${value}"]`)

    await chip('confirmed').trigger('click')
    expect(ids()).toEqual(['bp-1'])
    await chip('unverified').trigger('click')
    expect(ids()).toEqual(['bp-2', 'bp-3', 'bp-br'])
  })

  // The pane is unmounted while a sample is focused; the store it reads is
  // not, so coming back finds every choice where it was left.
  it('keeps every choice across a remount', async () => {
    wrapper = await mountPane({ peaks: peaks() })
    await wrapper.find('[data-testid="ledger-search"]').setValue('C6H')
    wrapper.vm.toggleTier('assigned')
    wrapper.vm.showIsotopologues = true
    wrapper.vm.onlyBlends = true
    wrapper.vm.badgeFilter = 'manual'
    wrapper.vm.verdictFilter = 'unverified'
    wrapper.vm.onlyFlatSeries = true
    wrapper.vm.onlyStripped = true
    wrapper.vm.onlyDisagreements = true
    await wrapper.vm.$nextTick()
    wrapper.unmount()

    wrapper = mount(PaneBrowserBatchPeaks, {
      global: { stubs: GLOBAL_STUBS, directives: { tooltip: {}, help: {} } }
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="ledger-search"]').element.value).toBe('C6H')
    expect([...wrapper.vm.activeTiers]).toEqual(['assigned'])
    expect(wrapper.vm.showIsotopologues).toBe(true)
    expect(wrapper.vm.onlyBlends).toBe(true)
    expect(wrapper.vm.badgeFilter).toBe('manual')
    expect(wrapper.vm.verdictFilter).toBe('unverified')
    expect(wrapper.vm.onlyFlatSeries).toBe(true)
    expect(wrapper.vm.onlyStripped).toBe(true)
    expect(wrapper.vm.onlyDisagreements).toBe(true)
  })

  it('keeps the engine view across a remount', async () => {
    wrapper = await mountPane({ peaks: peaks() })
    wrapper.vm.engineView = true
    await wrapper.vm.$nextTick()
    wrapper.unmount()

    wrapper = mount(PaneBrowserBatchPeaks, {
      global: { stubs: GLOBAL_STUBS, directives: { tooltip: {}, help: {} } }
    })
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.engineView).toBe(true)
  })

  it('reads every choice back after a reload, and the sample scope keeps its own', async () => {
    wrapper = await mountPane({ peaks: peaks() })
    await wrapper.find('[data-testid="ledger-search"]').setValue('200.1')
    wrapper.vm.toggleTier('candidate')
    await wrapper.vm.$nextTick()
    wrapper.unmount()

    // A fresh session over the same storage.
    setActivePinia(createPinia())
    wrapper = mount(PaneBrowserBatchPeaks, {
      global: { stubs: GLOBAL_STUBS, directives: { tooltip: {}, help: {} } }
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.searchQuery).toBe('200.1')
    expect(ids()).toEqual(['bp-2'])
    const stored = JSON.parse(localStorage.getItem('mascope.browser.match.ledgerView'))
    expect(stored.batch.tiers).toEqual(['candidate'])
    expect(stored.sample.search).toBe('')
    expect(stored.sample.tiers).toEqual([])
  })

  // A stored value the code no longer knows falls back to the default.
  it('falls back on a stored choice it does not know', async () => {
    app = makeApp({ peaks: peaks() })
    localStorage.clear()
    localStorage.setItem(
      'mascope.browser.match.ledgerView',
      JSON.stringify({ batch: { badge: 'retired', tiers: ['nonsense'], blends: 1, search: 'C6H' } })
    )
    setActivePinia(createPinia())
    wrapper = mount(PaneBrowserBatchPeaks, {
      global: { stubs: GLOBAL_STUBS, directives: { tooltip: {}, help: {} } }
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.badgeFilter).toBeNull()
    expect(wrapper.vm.activeTiers.size).toBe(0)
    expect(wrapper.vm.onlyBlends).toBe(false)
    expect(wrapper.vm.searchQuery).toBe('C6H')
  })

  // The engine view is the reader's switch: a ledger that reloads with engine
  // tiers arriving after the mount must not drop the chips set in it.
  it('keeps the chips across a reload whose engine tiers arrive after the mount', async () => {
    wrapper = await mountPane({
      peaks: [peak('bp-1', 'assigned', 0.9, { engine_tier: 'candidate' })]
    })
    wrapper.vm.engineView = true
    await wrapper.vm.$nextTick()
    wrapper.vm.toggleTier('candidate')
    await wrapper.vm.$nextTick()
    wrapper.unmount()

    app = makeApp({ peaks: [] })
    wrapper = mount(PaneBrowserBatchPeaks, {
      global: { stubs: GLOBAL_STUBS, directives: { tooltip: {}, help: {} } }
    })
    await wrapper.vm.$nextTick()
    app.data.batchPeak.list = [
      served(peak('bp-1', 'assigned', 0.9, { engine_tier: 'candidate' })),
      served(peak('bp-2', 'assigned', 0.8, { mz: 200.1, engine_tier: 'assigned' }))
    ]
    await wrapper.vm.$nextTick()

    expect([...wrapper.vm.activeTiers]).toEqual(['candidate'])
    expect(ids()).toEqual(['bp-1'])
  })
})

// The rows and their actions (docs/dev/assignment_ledger_and_card.md,
// sections 3.2 and 3.3), as this scope configures the shared ledger.
describe('PaneBrowserBatchPeaks rows and their actions', () => {
  const ledgerOf = () => wrapper.findComponent({ name: 'AssignmentLedger' })
  const ids = () => wrapper.vm.rows.map((row) => row.batch_peak_id)
  const PEAKS = () => [
    peak('bp-dim', 'assigned', 0.95, { mz: 100.1, max_intensity: 20, n_present: 9 }),
    peak('bp-bright', 'candidate', 0.6, { mz: 200.1, max_intensity: 9000, n_present: 2 }),
    peak('bp-line', 'candidate', 0.6, {
      mz: 201.1,
      max_intensity: 900,
      n_present: 2,
      isotopologue_of: 'bp-bright'
    }),
    peak('bp-mid', 'assigned', 0.9, { mz: 150.1, max_intensity: 400, n_present: 9 })
  ]

  // Owner's decision of 2026-11-22: intensity, descending, the brightest
  // sample's; the sample count no longer leads with a screen of equal rows.
  it('opens sorted by its max intensity, brightest first', async () => {
    wrapper = await mountPane({ peaks: PEAKS() })
    expect(wrapper.vm.sortField).toBe('max_intensity')
    expect(wrapper.vm.sortOrder).toBe(-1)
    expect(ids()).toEqual(['bp-bright', 'bp-mid', 'bp-dim'])
    expect(ledgerOf().props('intensityLabel')).toBe('Max intensity')
  })

  it("names the batch peak's ion in the Ion column", async () => {
    wrapper = await mountPane({ peaks: PEAKS() })
    expect(ledgerOf().props('fields').ion).toBe('consensus_ion_formula')
    expect(columnFor('consensus_ion_formula').props('sortable')).toBe(true)
  })

  // The batch's sample list is no denominator: a batch peak's partition holds
  // only the samples of its mode and instrument type that were folded in.
  it('counts the samples the species is seen in, alone', async () => {
    const cells = await cellsOf('n_present', PEAKS())
    // Twelve samples in the batch; the species is in 9 of its partition's.
    app.data.sample.list = Array.from({ length: 12 }, () => ({}))
    await wrapper.vm.$nextTick()
    expect(cells['bp-dim'].find('[data-testid="samples"]').text()).toBe('9')
    expect(wrapper.vm.samplesLabel({ n_present: 2 })).toBe('2')
    expect(wrapper.vm.samplesLabel({ n_present: null })).toBe('')
    expect(columnFor('n_present').props('sortable')).toBe(true)
  })

  // The m/z and the row's action open the batch peak in its brightest sample.
  it('opens the brightest sample from the m/z and from the row action', async () => {
    const { useBatchPeakJump } =
      await import('@/lib/panes/PaneBrowserMatch/stores/batchPeakJump.js')
    await cellsOf('', PEAKS())
    const jump = useBatchPeakJump()
    const spy = vi.spyOn(jump, 'jumpToBrightest').mockResolvedValue(undefined)
    const bright = wrapper.vm.rows.find((row) => row.batch_peak_id === 'bp-bright')

    expect(ledgerOf().props('mzAction')).toBe('Open the brightest sample with this peak in focus')
    ledgerOf().vm.$emit('open-mz', bright)
    expect(spy).toHaveBeenLastCalledWith(bright)

    spy.mockClear()
    const action = wrapper
      .findAll('.column-stub')
      .filter((col) => col.attributes('data-field') === undefined)
      .at(-1)
      .find('.stub-cell[data-row="bp-bright"] .jump-cell')
    await action.trigger('click')
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ batch_peak_id: 'bp-bright' }))

    // A batch peak in no sample has no sample to open.
    spy.mockClear()
    wrapper.vm.openBrightest({ ...bright, n_present: 0 })
    expect(spy).not.toHaveBeenCalled()
  })

  it("unfolds one family from its caret, and Show's switch sets them all again", async () => {
    wrapper = await mountPane({ peaks: PEAKS() })
    const bright = wrapper.vm.rows[0]
    expect(ledgerOf().props('familySize')(bright)).toBe(1)
    expect(ledgerOf().props('familyOpen')(bright)).toBe(false)

    ledgerOf().vm.$emit('toggle-family', bright)
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-bright', 'bp-line', 'bp-mid', 'bp-dim'])
    // A line folds nothing of its own.
    expect(ledgerOf().props('familySize')(wrapper.vm.rows[1])).toBe(0)

    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-bright', 'bp-line', 'bp-mid', 'bp-dim'])
    ledgerOf().vm.$emit('toggle-family', bright)
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-bright', 'bp-mid', 'bp-dim'])
  })

  it('draws the intensity bars over every batch peak shown', async () => {
    wrapper = await mountPane({ peaks: PEAKS() })
    wrapper.vm.toggleTier('assigned')
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-mid', 'bp-dim'])
    expect(ledgerOf().props('intensities')).toEqual([20, 9000, 900, 400])
  })

  // What the batch's own rules hold the tier down on: the cap mark on the chip.
  it('hands the tier chip the reasons that hold it down', async () => {
    const held = [{ rule: 'persistence_only', detail: 'searched because ...', caps: true }]
    const kept = [{ rule: 'second_line', detail: 'a line of ...', caps: false }]
    wrapper = await mountPane({
      peaks: [
        peak('bp-held', 'candidate', 0.6, { tier_reasons: held }),
        peak('bp-kept', 'assigned', 0.9, { mz: 300, tier_reasons: kept })
      ]
    })
    const cap = ledgerOf().props('tierCap')
    const [first, second] = wrapper.vm.rows
    expect(cap(first.batch_peak_id === 'bp-held' ? first : second).label).toBe('persistence only')
    expect(cap(first.batch_peak_id === 'bp-kept' ? first : second)).toBeNull()
  })
})

// The rows a reading spans (MAS-1069): the lens a badge or the xN pill opens,
// Plot the group, and Group by - on the fixture batch the backend's groups
// suite seeds (./ledger/groupBatch.js).
describe('PaneBrowserBatchPeaks groups', () => {
  const ledgerOf = () => wrapper.findComponent({ name: 'AssignmentLedger' })
  const ids = () => wrapper.vm.rows.map((row) => row.batch_peak_id)
  const labels = () => wrapper.vm.rows.map((row) => [row.batch_peak_id, row.lensLabel])
  let fixture
  beforeEach(async () => {
    fixture = await import('./ledger/groupBatch.js')
  })
  // The fixture's batch peaks, the series members with their series record
  // and an intensity each, brightest the heaviest.
  const PEAKS = () =>
    fixture.BATCH_PEAKS.map((row) => ({
      ...row,
      max_intensity: row.mz * 10,
      best_fit_score: 0.9,
      series: ['bp-up', 'bp-three'].includes(row.batch_peak_id)
        ? { unit: 'CH2', root_formula: 'C5H8O4', root_batch_peak_id: 'bp-root', step: 1 }
        : null
    }))
  const SERIES_ORDER = ['bp-down', 'bp-root', 'bp-up', 'lens:gap:2', 'bp-three']

  async function mountGroups(options = {}) {
    wrapper = await mountPane({
      peaks: PEAKS(),
      ledger: { groups: fixture.BATCH_GROUPS },
      ...options
    })
    return wrapper
  }
  const openSeries = async () => {
    wrapper.vm.openLens(fixture.SERIES_GROUP)
    await wrapper.vm.$nextTick()
  }

  it("opens the series lens from a row's series badge", async () => {
    const cells = await cellsOf('acrossRank', PEAKS())
    app.data.batchPeak.groups = fixture.BATCH_GROUPS
    await wrapper.vm.$nextTick()
    // The root carries no series record of its own: its badge is the step's.
    expect(cells['bp-root'].find('[data-testid="lens-series"]').exists()).toBe(false)
    await cells['bp-up'].find('[data-testid="lens-series"]').trigger('click')
    expect(ids()).toEqual(SERIES_ORDER)
  })

  it('narrows to the group in its order and labels each step', async () => {
    await mountGroups()
    await openSeries()
    expect(labels()).toEqual([
      ['bp-down', '−1 CH2'],
      ['bp-root', 'root'],
      ['bp-up', '+1 CH2'],
      ['lens:gap:2', '+2 CH2'],
      ['bp-three', '+3 CH2']
    ])
    expect(ledgerOf().props('lens')).toEqual({
      title: 'CH2 series from C5H8O4, through [M+NO3]-, 4 batch peaks, one step with no peak',
      column: 'Step',
      plot: true
    })
  })

  it('keeps the gap in the list, with the formula the step stands at', async () => {
    await mountGroups()
    await openSeries()
    // The ledger draws a pseudo-row greyed (assignmentLedger.spec.js).
    expect(wrapper.vm.rows.find((row) => row.ledgerKind === 'gap')).toMatchObject({
      lensLabel: '+2 CH2',
      pseudoFormula: 'C7H12O4',
      pseudoNote: 'no batch peak'
    })
  })

  it('lifts the tier filter while open and keeps it for after', async () => {
    await mountGroups()
    wrapper.vm.toggleTier('assigned')
    await wrapper.vm.$nextTick()
    const before = ids()
    expect(before).not.toContain('bp-down')
    await openSeries()
    expect(ids()).toEqual(SERIES_ORDER)
    ledgerOf().vm.$emit('close-lens')
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(before)
  })

  it('survives a sort: the members follow the column, the gap after them', async () => {
    await mountGroups()
    await openSeries()
    wrapper.vm.sortField = 'mz'
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()
    expect(ledgerOf().props('lens')).not.toBeNull()
    expect(ids()).toEqual(['bp-down', 'bp-root', 'bp-up', 'bp-three', 'lens:gap:2'])
  })

  it('gives back the sort it had before, after a sort picked under it', async () => {
    await mountGroups()
    const before = [wrapper.vm.sortField, wrapper.vm.sortOrder]
    const rowsBefore = ids()
    await openSeries()
    wrapper.vm.sortField = 'mz'
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()
    // A second lens opened over the first keeps the sort the first one found.
    wrapper.vm.openLens(fixture.CHANNEL_GROUP)
    await wrapper.vm.$nextTick()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect([wrapper.vm.sortField, wrapper.vm.sortOrder]).toEqual(before)
    expect(ids()).toEqual(rowsBefore)
  })

  it('closes on its x and on Escape, and the previous view comes back', async () => {
    await mountGroups()
    const before = ids()
    await openSeries()
    ledgerOf().vm.$emit('close-lens')
    await wrapper.vm.$nextTick()
    expect(ledgerOf().props('lens')).toBeNull()
    expect(ids()).toEqual(before)

    await openSeries()
    await wrapper.find('[data-testid="lens-close"]').trigger('click')
    expect(ids()).toEqual(before)

    await openSeries()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(ledgerOf().props('lens')).toBeNull()
    expect(ids()).toEqual(before)
  })

  it("Plot the group selects the group's batch peaks for the batch chart", async () => {
    await mountGroups()
    app.data.batchPeak.selected = [fixture.BATCH_PEAKS.find((bp) => bp.batch_peak_id === 'bp-twin')]
    await openSeries()
    await wrapper.find('[data-testid="lens-bar"] .menu-button').trigger('click')
    expect(app.data.batchPeak.selected.map((bp) => bp.batch_peak_id)).toEqual([
      'bp-down',
      'bp-root',
      'bp-up',
      'bp-three'
    ])
  })

  it('opens the channels of a neutral from the xN pill, counted on the served group', async () => {
    const cells = await cellsOf('consensus_ion_formula', PEAKS())
    app.data.batchPeak.groups = fixture.BATCH_GROUPS
    await wrapper.vm.$nextTick()
    const pill = cells['bp-up'].find('[data-testid="channel-pill"]')
    expect(pill.text()).toBe('×2')
    // A neutral read through one channel carries none.
    expect(cells['bp-root'].find('[data-testid="channel-pill"]').exists()).toBe(false)
    await pill.trigger('click')
    expect(labels()).toEqual([
      ['bp-up_other', '[M-H]-'],
      ['bp-up', '[M+NO3]-']
    ])
    expect(ledgerOf().props('lens').column).toBe('Channel')
  })

  it("opens the 14N twin from the root's batch facts", async () => {
    await mountGroups()
    wrapper.vm.openLens(wrapper.vm.badgeGroup({ batch_peak_id: 'bp-twin' }, 'facts'))
    await wrapper.vm.$nextTick()
    expect(labels()).toEqual([
      ['bp-root', '[M+NO3]-'],
      ['bp-twin', '14N twin']
    ])
  })

  // The fixture's isotope lines as the read serves them: each folded under its
  // compound by `line_owner_id`.
  const LINED = () =>
    PEAKS().map((row) =>
      row.isotopologue_of ? { ...row, line_owner_id: row.isotopologue_of } : row
    )

  it('lists a member that is an isotope line as the line, not as missing', async () => {
    // The halogen lock names the root's 13C line, which folds under the root.
    await mountGroups({ peaks: LINED() })
    expect(ids()).not.toContain('bp-root_line')
    wrapper.vm.openLens(fixture.HALOGEN_GROUP)
    await wrapper.vm.$nextTick()
    expect(labels()).toEqual([
      ['bp-root', '[M+NO3]-'],
      ['bp-root_line', 'halogen line']
    ])
    expect(wrapper.vm.rows.some((row) => row.ledgerKind === 'absent')).toBe(false)
    expect(ledgerOf().props('lens').title).toBe('C5H8O4 and its halogen line, 2 batch peaks')

    // Unfolded, the root's family lists the line once: where the group places it.
    ledgerOf().vm.$emit('toggle-family', wrapper.vm.rows[0])
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-root', 'bp-root_line'])
  })

  it("unfolds a member's family under it in the lens", async () => {
    await mountGroups({ peaks: LINED() })
    wrapper.vm.openLens(fixture.CHANNEL_GROUP)
    await wrapper.vm.$nextTick()
    const up = wrapper.vm.rows.find((row) => row.batch_peak_id === 'bp-up')
    ledgerOf().vm.$emit('toggle-family', up)
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-up_other', 'bp-up', 'bp-up_line'])
  })

  it("closes on another batch: a lens is the batch's it was opened on", async () => {
    await mountGroups()
    await openSeries()
    app.data.batch.focusedId = 'b-2'
    await wrapper.vm.$nextTick()
    expect(ledgerOf().props('lens')).toBeNull()
  })

  it('offers Group by none, compound and series', async () => {
    await mountGroups()
    const choice = wrapper
      .findAllComponents({ name: 'LedgerMenuChoice' })
      .find((c) => c.props('label') === 'Group by')
    expect(choice.props('options').map((o) => o.value)).toEqual(['none', 'compound', 'series'])
  })

  const lines = () =>
    wrapper.vm.rows.map((row) =>
      row.ledgerKind === 'group'
        ? `${row.groupName} | ${row.groupCount} | ${row.groupTier}`
        : row.ledgerKind
          ? `${row.ledgerKind}:${row.pseudoFormula}`
          : row.batch_peak_id
    )

  it('groups by series: a group line over its members, the rest Ungrouped', async () => {
    await mountGroups()
    wrapper.vm.view.groupBy = 'series'
    await wrapper.vm.$nextTick()
    expect(lines()).toEqual([
      'CH2 series from C5H8O4, through [M+NO3]- | 4 batch peaks | assigned',
      'bp-three',
      'bp-up',
      'bp-root',
      'bp-down',
      'Ungrouped | 3 batch peaks | null',
      'bp-two_other',
      'bp-twin',
      'bp-up_other'
    ])
  })

  it('lists the rows the search keeps, though no group holds one of them', async () => {
    await mountGroups()
    wrapper.vm.view.groupBy = 'series'
    // The 14N twin is in no series.
    wrapper.vm.searchQuery = 'C5H9NO4'
    await wrapper.vm.$nextTick()
    expect(lines()).toEqual(['Ungrouped | 1 batch peak | null', 'bp-twin'])
  })

  it('groups by compound, and a group line folds its members', async () => {
    await mountGroups()
    wrapper.vm.view.groupBy = 'compound'
    await wrapper.vm.$nextTick()
    expect(lines().slice(0, 3)).toEqual([
      'C6H10O4 | 2 batch peaks | assigned',
      'bp-up',
      'bp-up_other'
    ])
    expect(lines()[3]).toBe('Ungrouped | 5 batch peaks | null')

    ledgerOf().vm.$emit('toggle-group', wrapper.vm.rows[0])
    await wrapper.vm.$nextTick()
    expect(lines()[1]).toBe('Ungrouped | 5 batch peaks | null')
    expect(wrapper.vm.rows[0].collapsed).toBe(true)
  })

  it('follows the group as the newest read serves it', async () => {
    await mountGroups()
    await openSeries()
    // A reload serves the series with a member two steps up: the gap is gone.
    app.data.batchPeak.list = [
      ...app.data.batchPeak.list,
      { ...fixture.BATCH_PEAKS[0], batch_peak_id: 'bp-two', consensus_formula: 'C7H12O4', mz: 222 }
    ]
    app.data.batchPeak.groups = [
      {
        ...fixture.SERIES_GROUP,
        members: [
          ...fixture.SERIES_GROUP.members,
          { ...fixture.member('up'), batch_peak_id: 'bp-two', formula: 'C7H12O4', mz: 222, step: 2 }
        ]
      }
    ]
    await wrapper.vm.$nextTick()
    expect(ids()).toEqual(['bp-down', 'bp-root', 'bp-up', 'bp-two', 'bp-three'])
  })

  it('lists a member of the other instrument type greyed, as not in this ledger', async () => {
    await mountGroups({
      ledger: {
        groups: fixture.BATCH_GROUPS,
        inPartition: (bp) => bp.batch_peak_id !== 'bp-three'
      }
    })
    await openSeries()
    expect(wrapper.vm.rows.at(-1)).toMatchObject({
      ledgerKind: 'absent',
      lensLabel: '+3 CH2',
      pseudoNote: 'not in this ledger',
      pseudoMz: 236
    })
    // The batch scope counts it among the batch peaks: it is not a sample's.
    expect(ledgerOf().props('lens').title).toBe(
      'CH2 series from C5H8O4, through [M+NO3]-, 4 batch peaks, one step with no peak'
    )
  })

  it('names a row in two series again under the second, greyed', async () => {
    const OXYGEN = {
      group_id: 'series:bp-o:O:mech',
      kind: 'series',
      unit: 'O',
      root_batch_peak_id: 'bp-twin',
      root_formula: 'C5H9NO3',
      members: [fixture.member('twin', { step: 0 }), fixture.member('up', { step: 1 })]
    }
    await mountGroups({ ledger: { groups: [fixture.SERIES_GROUP, OXYGEN] } })
    wrapper.vm.view.groupBy = 'series'
    await wrapper.vm.$nextTick()
    const second = wrapper.vm.rows.findIndex((row) => row.groupKey === OXYGEN.group_id)
    expect(wrapper.vm.rows.slice(second + 1, second + 3)).toMatchObject([
      { batch_peak_id: 'bp-twin' },
      {
        ledgerKind: 'reference',
        pseudoFormula: 'C6H10O4',
        pseudoNote: 'listed under CH2 series from C5H8O4, through [M+NO3]-'
      }
    ])
  })

  it('unfolds every group line again when the grouping changes', async () => {
    await mountGroups()
    wrapper.vm.view.groupBy = 'series'
    await wrapper.vm.$nextTick()
    ledgerOf().vm.$emit('toggle-group', wrapper.vm.rows[0])
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.rows[0].collapsed).toBe(true)
    wrapper.vm.view.groupBy = 'compound'
    await wrapper.vm.$nextTick()
    wrapper.vm.view.groupBy = 'series'
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.rows[0].collapsed).toBe(false)
  })

  it('draws no sample count and no badges on a group line', async () => {
    const samples = await cellsOf('n_present', PEAKS())
    app.data.batchPeak.groups = fixture.BATCH_GROUPS
    wrapper.vm.view.groupBy = 'series'
    await wrapper.vm.$nextTick()
    void samples
    const cellOf = (field, id) =>
      wrapper
        .findAll('.column-stub')
        .find((col) => col.attributes('data-field') === field)
        .find(`.stub-cell[data-row="${id}"]`)
    const line = wrapper.vm.rows[0].batch_peak_id
    expect(cellOf('n_present', line).find('[data-testid="samples"]').exists()).toBe(false)
    expect(cellOf('acrossRank', line).find('[data-testid="across"]').exists()).toBe(false)
    expect(cellOf('n_present', 'bp-up').find('[data-testid="samples"]').exists()).toBe(true)
  })

  it('selects no group line, gap or reference row', async () => {
    await mountGroups()
    wrapper.vm.view.groupBy = 'series'
    await wrapper.vm.$nextTick()
    wrapper.vm.applySelection(wrapper.vm.rows)
    expect(app.data.batchPeak.selected.every((row) => !row.ledgerKind)).toBe(true)
    expect(app.data.batchPeak.selected).toHaveLength(7)
  })
})
