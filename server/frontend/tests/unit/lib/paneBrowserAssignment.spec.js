import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// The real badge, imported past the `@/lib/base` barrel mock below: what the
// ledger's verdict column renders for a row with no verdict is half of the
// formula-less-row guard, and a stub could not tell us.
import BaseVerdictBadge from '@/lib/base/BaseVerdictBadge.vue'
// The real store, not a stub: it is the whole channel between the Assign-peaks
// button in the switch bar and the dialog this pane owns.
import { useAssignmentLauncher } from '@/lib/panes/PaneBrowserMatch/stores'
import { usePeakAssignParams } from '@/lib/peakAssignParams'
import { isIsotopeLine } from '@/lib/isotopeLines'
import { TIERING_PROVISIONAL, tierRank } from '@/lib/tiers'
import { LEVELS } from '@/lib/levels'
import { SORT_FIELDS } from '@/lib/ledger/useLedgerView.js'

// The per-sample launcher's job after the assign endpoint became synchronous:
// a run that is refused (409) or a sample that cannot be assigned (422) arrives
// as a rejection, and must land as a readable reason in the pane rather than an
// uncaught promise behind a dialog that never closes.

const assign = vi.fn()
// The run list read again after a launch, so the new run's status holds the
// button (lib/panes/PaneBrowserMatch/stores/assignmentLauncher.js).
const runLoad = vi.fn()
// Module-level so assertions see the same mock the component called: makeApp()
// runs afresh on every useApp() and would otherwise hand out new spies.
const sampleUnfocus = vi.fn()
const peakUnfocus = vi.fn()
const peakFocus = vi.fn()
// Reads `verdictRecord` at call time, so a test can set it after mounting.
// `verdictPeakId`, when a test sets one, additionally models the real store's
// identity lookup: a record is found only for the peak it was recorded against.
// Records only ever carry an M0's `sample_peak_id`, so that is what makes the
// family-resolution tests below non-vacuous - without it every row would answer
// with the same verdict whether it was resolved to its M0 or not.
const forAssignment = vi.fn((row) =>
  verdictPeakId && row?.sample_peak_id !== verdictPeakId ? null : verdictRecord
)
// The batch-level verdict reaching a row with none of its own: null unless a
// test sets one, on the M0 whose peak id it names.
let overlayRecord = null
const overlayFor = vi.fn((row) =>
  overlayRecord && row?.sample_peak_id === overlayRecord.sample_peak_id ? overlayRecord : null
)

const SAMPLE = { sample_item_id: 'si-1', sample_item_name: 'Sample 1' }
const BATCH = { sample_batch_id: 'sb-1', sample_batch_name: 'Batch 1' }
// The focused batch's saved assignment settings (`assignment_settings`), which
// the Assign sample dialog opens on; null is the defaults.
let batchSettings = null
// The server defaults /params serves, which every field the batch did not save
// opens on.
const SERVED = {
  run_untargeted: true,
  profile: 'auto',
  context: 'auto',
  mz_precision_ppm: null,
  formula_ranges: null,
  max_untargeted_peaks: null,
  peak_intensity_threshold: 0,
  max_alternatives: 5
}

let focusedSampleId
// The peak focused elsewhere (spectrum, chart, jump), or null.
let focusedPeak
// The focused sample's peaks, as the peak store lists them, and whether that
// list is still loading.
let peakList
let peaksPending
// Bumped to tell the pane the three above changed after it mounted.
const peakRev = ref(0)
// The row a hand assignment wrote, as the store announces it once its reload
// is in (`written`), or null.
const writtenRef = ref(null)
// The peak list's load error, or null.
let peaksError
// Whether the run's rows are still loading, and their load error, or null.
let assignmentsPending
let assignmentsError
let runList
let runError
let assignmentList
let childrenByOwner
let byId
let verdictRecord
let verdictPeakId
// The focused sample's run record; `{ engine: 'batch' }` is a sample served
// from the batch ledger rather than from a run of its own.
let runRecord
// What the store's histogram answers for the strip above the table.
let tierCountsRecord
// The run's verification queue, in the server's order.
let queueList
// The detail row a click on a pill reads (`loadDetail`), and the mechanisms
// the Ion column names.
let detailRecord = null
let mechanismList = []
// The focused batch's Batch peaks ledger: empty is a batch with none yet.
let batchPeakList = []

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
        get focusedId() {
          return focusedSampleId.value
        },
        set focusedId(id) {
          focusedSampleId.value = id
        },
        unfocus: sampleUnfocus
      },
      batch: {
        focused: { ...BATCH, assignment_settings: batchSettings },
        focusedId: BATCH.sample_batch_id
      },
      peak: {
        // Read live, through `peakRev`: a test that lands the peaks after
        // mounting bumps it, so the pane's watchers see the change.
        get list() {
          peakRev.value
          return peakList
        },
        get pending() {
          peakRev.value
          return peaksPending
        },
        get error() {
          peakRev.value
          return peaksError
        },
        focused: focusedPeak,
        focus: peakFocus,
        unfocus: peakUnfocus
      },
      batchPeak: {
        list: batchPeakList,
        selected: [],
        isSelected: () => false,
        select: vi.fn(),
        unselect: vi.fn()
      },
      ionization: { mechanism: { list: mechanismList } },
      peakAssignment: {
        run: {
          pending: false,
          list: runList,
          error: runError,
          focused: runList[0] ?? null,
          focus: vi.fn(),
          unfocus: vi.fn(),
          assign,
          load: runLoad
        },
        peak: {
          list: assignmentList,
          pending: assignmentsPending,
          error: assignmentsError,
          tierCounts: tierCountsRecord,
          childrenOf: (id) => childrenByOwner.get(id) ?? [],
          // Stands in for the store's family resolution; the rule itself is
          // pinned against the real implementation in
          // stores/data/modules/peakAssignment/assignment.spec.js.
          m0Of: (row) =>
            isIsotopeLine(row) ? (byId.get(row.owner_peak_assignment_id) ?? row) : row,
          forPeak: (peakId) =>
            assignmentList.find((row) => String(row.sample_peak_id) === String(peakId)) ?? null,
          loadDetail: async () => detailRecord,
          run: runRecord,
          get written() {
            return writtenRef.value
          }
        },
        verification: { forAssignment },
        anchorContext: { overlayFor },
        queue: { list: queueList, count: queueList.length }
      }
    },
    ui: { tab: { active: 'sample' }, help: helpStub, notification: { push: vi.fn() } }
  }
}

vi.mock('@/stores', () => ({ useApp: () => makeApp() }))

vi.mock('@/lib/base', async () => ({
  BaseTabbedPanel: { template: '<div><slot name="menu" /><slot /></div>' },
  // Renders what it would copy, so a test that renders the cells can read the
  // formula column.
  BaseCopyableField: {
    props: ['field', 'tooltip'],
    template: '<span class="copyable" :data-tooltip="tooltip">{{ field }}<slot /></span>'
  },
  BaseLoadError: true,
  // Says what the empty state says, and carries its way forward.
  BaseEmptyState: {
    props: ['icon', 'title', 'hint', 'note'],
    template: '<div class="empty-state-stub">{{ title }} {{ hint }}<slot name="actions" /></div>'
  },
  // Says which evidence it was handed, so the queue's tests can read it off.
  BaseTierTag: {
    props: ['tier', 'evidence', 'role', 'source', 'tooltip', 'cap'],
    template:
      '<span class="tier-tag-stub" :data-evidence="String(evidence)" :data-cap="cap?.label" ' +
      ':data-role="role" />'
  },
  // Says what it was handed, so the level column's tests can read it off; the
  // chip has a spec of its own (base/BaseLevelTag.spec.js).
  BaseLevelTag: {
    props: ['level', 'evidence', 'lift', 'role', 'tooltip', 'source', 'engineLevel'],
    template:
      '<span class="level-tag-stub" :data-level="level" :data-evidence="evidence" ' +
      ':data-lift="lift" :data-role="role" :data-source="source" ' +
      ':data-engine-level="engineLevel" />'
  },
  BaseVerdictBadge: true,
  BaseEvidenceBadge: (await vi.importActual('@/lib/base/BaseEvidenceBadge.vue')).default,
  // Real: the tier column's header renders it, and the provisional-tiers tests
  // below read the mark it draws.
  BaseProvisionalMark: (await vi.importActual('@/lib/base/BaseProvisionalMark.vue')).default,
  // Real: the point of the run-provenance test below is that the pane hands
  // this component each run, which a stub could not tell us.
  BaseRunProvenance: (await vi.importActual('@/lib/base/BaseRunProvenance.vue')).default
}))

vi.mock('@/lib/dialogs', () => ({
  PeakAssignConfigForm: {
    name: 'PeakAssignConfigForm',
    props: ['hidden', 'sampleItemId', 'sampleBatchId', 'model'],
    methods: { commit: () => formCommits },
    template:
      '<div class="config-form-stub" :data-sample="sampleItemId" :data-batch="sampleBatchId" />'
  }
}))

// The pane imports the shared parameter store, which reaches for /params.
// The launcher dialog's form is stubbed above, so nothing here fetches it;
// the tests that care seed the store directly.
vi.mock('@/api', () => ({
  api: {
    http: {
      get: () => Promise.resolve({ data: { data: { params: { peak_assignment: SERVED } } } })
    }
  }
}))
// What the stubbed form's commit() answers: false is a range that does not parse.
let formCommits = true
// The row's verdict form has a spec of its own; here it only has to show
// which row the cell opened it for.
vi.mock('@/lib/ledger/LedgerVerdictPopover.vue', () => ({
  default: {
    name: 'LedgerVerdictPopover',
    props: ['scope', 'row', 'record', 'overlay'],
    template: '<div class="verdict-popover-stub">{{ row?.peak_assignment_id }}</div>'
  }
}))
// The scroll into view is the helper's business (lib/virtualScroll.spec.js);
// here what matters is that the pane asks for the focused row's display index.
const scrollVirtualRowIntoView = vi.fn()
vi.mock('@/lib/virtualScroll', () => ({
  scrollVirtualRowIntoView: (...args) => scrollVirtualRowIntoView(...args)
}))

const GLOBAL_STUBS = {
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
  Dialog: { template: '<div><slot /><slot name="footer" /></div>' },
  Message: { template: '<div class="pane-message"><slot /></div>' },
  Button: { template: '<button><slot /></button>' },
  // Mandatory, not a convenience: a real Popover renders `<!---->` until it is
  // opened and then teleports its content to document.body, where wrapper
  // queries cannot reach it. Rendering the slot inline is what lets a test see
  // the controls the menu holds - `open` says whether the pane asked for it.
  Popover: {
    data: () => ({ open: false }),
    emits: ['show', 'hide'],
    methods: {
      toggle() {
        this.open = !this.open
        this.$emit(this.open ? 'show' : 'hide')
      },
      show() {
        this.open = true
        this.$emit('show')
      },
      hide() {
        this.open = false
        this.$emit('hide')
      }
    },
    template: '<div class="view-menu-popover"><slot /></div>'
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
      sortOrder: { type: Number, default: null },
      rowClass: { type: Function, default: null }
    },
    inheritAttrs: false,
    template: '<div class="datatable"><slot /></div>'
  },
  Column: true,
  // The Plot box: says whether it is ticked and offers its change on a click.
  Checkbox: {
    name: 'Checkbox',
    props: ['modelValue', 'disabled', 'inputId', 'ariaLabel', 'binary'],
    emits: ['update:modelValue'],
    template:
      '<button class="plot-box-stub" :data-checked="String(modelValue)" :disabled="disabled" ' +
      ':aria-label="ariaLabel" @click="$emit(\'update:modelValue\', !modelValue)" />'
  },
  ProgressSpinner: true,
  // Rendered rather than stubbed away, so the switch a test flips is the one
  // the pane binds: `ToggleSwitch: true` renders an element with no v-model,
  // which is how the control could have been left unwired behind the new menu
  // with every row-level test still green.
  ToggleSwitch: {
    name: 'ToggleSwitch',
    // `inputId` and `pt` are declared so a test can read what the pane asked
    // for: the id is the other half of the `<label for>` pairing, and the pt is
    // the autofocus that makes the popover reachable by keyboard at all.
    props: ['modelValue', 'inputId', 'pt'],
    emits: ['update:modelValue'],
    template:
      '<button class="iso-toggle" :id="inputId" ' +
      '@click="$emit(\'update:modelValue\', !modelValue)">{{ modelValue }}</button>'
  }
}

/** An axios-shaped rejection carrying a server message. */
function apiError(status, message) {
  return { response: { status, data: { error: message } } }
}

const { default: PaneBrowserAssignment } =
  await import('@/lib/panes/PaneBrowserMatch/PaneBrowserAssignment.vue')

// The pill's lens reads the detail row, a promise.
const flushPromisesLocal = () => new Promise((resolve) => setTimeout(resolve, 0))

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

/** An M0 assignment with its isotopologues, as the ledger sees them. */
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
    // The engine copies the M0's formula and mechanism onto every isotopologue, so
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

// Two families whose isotopologues are nowhere near their parent on any axis: A's
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
 *  isotopologues in m/z order; otherwise the reason it is not. */
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
        return `isotopologues of ${owner} are not in m/z order`
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

// Every pane a test mounts is unmounted after it. The pane's stubs read refs this
// file shares (the written row, the focused sample), so a pane left mounted
// renders again when a later test writes one, against data that test replaced.
enableAutoUnmount(afterEach)

// Reset before every test in the file, including the describes below that only
// override part of it.
beforeEach(() => {
  // The pane reads the Assign-peaks dialog's open flag from a real Pinia store
  // (the button that sets it lives a row up, in the switch bar). A fresh Pinia
  // per test keeps that flag from leaking between them.
  // The ledger's sort store writes through to localStorage and reads it back on
  // creation, so without this a sort set in one test would be the next test's
  // starting order.
  localStorage.clear()
  setActivePinia(createPinia())
  focusedSampleId = ref('si-1')
  focusedPeak = null
  peakList = []
  peaksPending = false
  peaksError = null
  assignmentsPending = false
  assignmentsError = null
  scrollVirtualRowIntoView.mockClear()
  runList = []
  runError = null
  verdictRecord = null
  verdictPeakId = null
  overlayRecord = null
  runRecord = null
  tierCountsRecord = {}
  queueList = []
  batchPeakList = []
  writtenRef.value = null
  seed()
})

describe('PaneBrowserAssignment launcher', () => {
  beforeEach(() => {
    batchSettings = null
    formCommits = true
    focusedSampleId = ref('si-1')
    runList = []
    assign.mockReset()
    assign.mockResolvedValue({ data: [{ peak_assignment_run_id: 'run-1' }] })
  })
  afterEach(() => vi.clearAllMocks())

  // The form the dialog renders, and the model it is bound to.
  const formModel = (wrapper) =>
    wrapper.findComponent({ name: 'PeakAssignConfigForm' }).props('model')

  async function openDialog(wrapper) {
    wrapper.vm.configVisible = true
    await flushAll()
  }
  const flushAll = () => new Promise((resolve) => setTimeout(resolve, 0))

  it("opens on the batch's settings and launches with them", async () => {
    // The batch's saved settings, not the per-user store's: a sample is
    // assigned as its batch is unless the dialog is changed.
    batchSettings = { mz_precision_ppm: 4, max_alternatives: 2, profile: 'BR' }
    const store = usePeakAssignParams()
    store.params.mz_precision_ppm = 9

    const wrapper = await mountPane()
    await openDialog(wrapper)
    // The form names the chemistry the run would use on the sample it is for.
    const form = wrapper.find('.config-form-stub')
    expect(form.attributes('data-sample')).toBe('si-1')
    expect(form.attributes('data-batch')).toBeUndefined()
    expect({ ...formModel(wrapper) }).toEqual({
      ...SERVED,
      mz_precision_ppm: 4,
      max_alternatives: 2,
      profile: 'BR'
    })

    await wrapper.vm.launch()

    expect(assign).toHaveBeenCalledTimes(1)
    expect(assign.mock.calls[0][0]).toBe('si-1')
    // Every field that is set, so the run records what it ran on; the
    // blank ones are left to the backend default.
    expect(assign.mock.calls[0][1]).toEqual({
      run_untargeted: true,
      profile: 'BR',
      context: 'auto',
      mz_precision_ppm: 4,
      peak_intensity_threshold: 0,
      max_alternatives: 2
    })
    expect(wrapper.vm.launchError).toBeNull()
    expect(wrapper.vm.configVisible).toBe(false)
  })

  it('opens on the defaults where the batch saved none', async () => {
    batchSettings = null
    const wrapper = await mountPane()
    await openDialog(wrapper)
    expect({ ...formModel(wrapper) }).toEqual(SERVED)
  })

  it("sends an override in the run's request, for that run alone", async () => {
    batchSettings = { max_untargeted_peaks: 100 }
    const store = usePeakAssignParams()
    const wrapper = await mountPane()
    await openDialog(wrapper)

    formModel(wrapper).max_untargeted_peaks = 42
    formModel(wrapper).run_untargeted = false
    await wrapper.vm.launch()
    expect(assign.mock.calls[0][1].max_untargeted_peaks).toBe(42)
    expect(assign.mock.calls[0][1].run_untargeted).toBe(false)

    // Not written back: the batch keeps its settings, the per-user store
    // its own, and the next open is on the batch's settings again.
    expect(batchSettings).toEqual({ max_untargeted_peaks: 100 })
    expect(store.params).not.toHaveProperty('max_untargeted_peaks')
    await openDialog(wrapper)
    expect(formModel(wrapper).max_untargeted_peaks).toBe(100)
    expect(formModel(wrapper).run_untargeted).toBe(true)
  })

  // The tier bands, the stage B confidence and the rest a batch may save
  // through the API: the form does not show them, the run still takes them.
  it('launches with what the batch saved of the fields the form does not show', async () => {
    batchSettings = { assigned_threshold: 0.9, stage_b_confidence: 0.5, max_alternatives: 2 }
    const wrapper = await mountPane()
    await openDialog(wrapper)

    formModel(wrapper).max_alternatives = 7
    await wrapper.vm.launch()

    expect(assign.mock.calls[0][1]).toMatchObject({
      assigned_threshold: 0.9,
      stage_b_confidence: 0.5,
      max_alternatives: 7
    })
  })

  it('keeps the dialog open while the typed range does not parse, and says why', async () => {
    const wrapper = await mountPane()
    await openDialog(wrapper)
    formCommits = false
    await wrapper.vm.launch()
    await wrapper.vm.$nextTick()
    expect(assign).not.toHaveBeenCalled()
    expect(wrapper.vm.configVisible).toBe(true)
    expect(wrapper.find('[data-testid="assign-form-error"]').text()).toContain(
      'The formula range does not parse'
    )

    // Fixed, the launch goes ahead and the reason goes away.
    formCommits = true
    await wrapper.vm.launch()
    await wrapper.vm.$nextTick()
    expect(assign).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="assign-form-error"]').exists()).toBe(false)
  })

  it('opens again without the last reason', async () => {
    const wrapper = await mountPane()
    await openDialog(wrapper)
    formCommits = false
    await wrapper.vm.launch()
    wrapper.vm.configVisible = false
    await flushAll()

    await openDialog(wrapper)
    expect(wrapper.find('[data-testid="assign-form-error"]').exists()).toBe(false)
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

  it("opens on the batch's settings when the dialog was opened before the pane mounted", async () => {
    // The inspector card's Assign sample, from Targets mode: it switches the
    // browser to this ledger and opens the flag in one go, so this pane mounts
    // with the flag already true and its watcher never sees it change.
    batchSettings = { mz_precision_ppm: 4, max_alternatives: 2, profile: 'BR' }
    useAssignmentLauncher().configVisible = true

    const wrapper = await mountPane()
    await flushAll()

    expect(wrapper.vm.configVisible).toBe(true)
    expect({ ...formModel(wrapper) }).toEqual({
      ...SERVED,
      mz_precision_ppm: 4,
      max_alternatives: 2,
      profile: 'BR'
    })
    expect(helpStub.set).toHaveBeenLastCalledWith('dialog_peak_assign')
    await wrapper.vm.launch()
    expect(assign.mock.calls[0][1]).toMatchObject({ mz_precision_ppm: 4, profile: 'BR' })
  })

  it('does nothing without a focused sample', async () => {
    focusedSampleId = ref(null)
    const wrapper = await mountPane()

    await wrapper.vm.launch()

    expect(assign).not.toHaveBeenCalled()
  })

  // A run of the sample still in flight holds it: the server refuses another
  // as busy, so neither the bar's button nor the dialog's own sends one.
  describe('while a run of the sample is in flight', () => {
    const confirm = (wrapper) => wrapper.find('[data-testid="assign-confirm"]')
    const deferred = () => {
      let resolve
      const promise = new Promise((r) => (resolve = r))
      return { promise, resolve }
    }

    beforeEach(() => runLoad.mockReset().mockResolvedValue(undefined))

    it.each(['pending', 'running', 'importing'])(
      'disables the confirm button and sends nothing for a %s run',
      async (status) => {
        runList = [{ peak_assignment_run_id: 'run-1', status }]
        const wrapper = await mountPane()
        await openDialog(wrapper)

        expect(confirm(wrapper).attributes('disabled')).toBeDefined()
        await wrapper.vm.launch()
        expect(assign).not.toHaveBeenCalled()
      }
    )

    it.each(['completed', 'failed', 'cancelled'])(
      'offers the confirm button once the run is %s',
      async (status) => {
        runList = [{ peak_assignment_run_id: 'run-1', status }]
        const wrapper = await mountPane()
        await openDialog(wrapper)

        expect(confirm(wrapper).attributes('disabled')).toBeUndefined()
        await wrapper.vm.launch()
        expect(assign).toHaveBeenCalledTimes(1)
      }
    )

    it('offers it for a sample with no run yet, as does the empty state', async () => {
      const wrapper = await mountPane()
      await openDialog(wrapper)

      expect(confirm(wrapper).attributes('disabled')).toBeUndefined()
      const empty = wrapper.find('.empty-state-stub [label="Assign sample"]')
      expect(empty.attributes('disabled')).toBeUndefined()
    })

    it('sends one request for a double click', async () => {
      const request = deferred()
      assign.mockReturnValue(request.promise)
      const wrapper = await mountPane()
      await openDialog(wrapper)

      const first = wrapper.vm.launch()
      await wrapper.vm.launch()
      expect(assign).toHaveBeenCalledTimes(1)

      request.resolve({ data: [{ peak_assignment_run_id: 'run-1' }] })
      await first
      expect(assign).toHaveBeenCalledTimes(1)
    })

    it('holds the sample until the run list has the new run', async () => {
      const read = deferred()
      runLoad.mockReturnValue(read.promise)
      const launcher = useAssignmentLauncher()
      const wrapper = await mountPane()
      await openDialog(wrapper)

      const launching = wrapper.vm.launch()
      expect(launcher.launchingId).toBe('si-1')
      await flushAll()

      // The request is answered and the dialog closed, but the list has not
      // been read yet: a click now would still be refused, so none is offered.
      expect(assign).toHaveBeenCalledTimes(1)
      expect(runLoad).toHaveBeenCalledTimes(1)
      expect(wrapper.vm.configVisible).toBe(false)
      expect(launcher.launchingId).toBe('si-1')
      const empty = wrapper.find('.empty-state-stub [label="Assign sample"]')
      expect(empty.attributes('disabled')).toBeDefined()
      expect(empty.attributes('loading')).toBeDefined()
      await wrapper.vm.launch()
      expect(assign).toHaveBeenCalledTimes(1)

      read.resolve()
      await launching
      expect(launcher.launchingId).toBeNull()
    })

    it('reads the list again for a refusal as busy, holding the sample until it lands', async () => {
      // A 409 means a run is in flight that the list does not have yet
      // (another person's, or one started at ingest): reading it holds the button.
      const read = deferred()
      runLoad.mockReturnValue(read.promise)
      assign.mockRejectedValue(apiError(409, 'busy'))
      const launcher = useAssignmentLauncher()
      const wrapper = await mountPane()

      const launching = wrapper.vm.launch()
      await flushAll()
      expect(runLoad).toHaveBeenCalledTimes(1)
      expect(launcher.launchingId).toBe('si-1')

      read.resolve()
      await launching
      expect(launcher.launchingId).toBeNull()
    })

    it('reads nothing again and lets go for a refusal that is not busy', async () => {
      assign.mockRejectedValue(apiError(422, 'bad range'))
      const launcher = useAssignmentLauncher()
      const wrapper = await mountPane()

      await wrapper.vm.launch()

      expect(runLoad).not.toHaveBeenCalled()
      expect(launcher.launchingId).toBeNull()
    })

    it("leaves another sample's hold alone when its own read lands", async () => {
      // Launch A, move to B and launch B before A's read lands: A's end must
      // not clear the hold B's launch took.
      const read = deferred()
      runLoad.mockReturnValue(read.promise)
      const launcher = useAssignmentLauncher()
      const wrapper = await mountPane()

      const launching = wrapper.vm.launch()
      await flushAll()
      expect(launcher.launchingId).toBe('si-1')
      launcher.launchingId = 'si-2'

      read.resolve()
      await launching
      expect(launcher.launchingId).toBe('si-2')
    })
  })
})

// Run provenance moved out with the run selector, to
// tests/unit/lib/panes/PaneBrowserMatch/assignmentRunBar.spec.js.

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
// is handed and would scatter every isotopologue away from the parent
// whose formula names it. These assert on `rows`, which with `lazy` set is
// exactly the order the table renders.
describe('PaneBrowserAssignment isotopologue grouping', () => {
  const SORTABLE_COLUMNS = [
    'sample_peak_mz',
    'sample_peak_intensity',
    'assigned_formula',
    'mech',
    'tierRank'
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

  it('keeps each isotopologue under its own parent when sorting by intensity', async () => {
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

  // The sort lives in a store outside the pane: PaneBrowserMatch swaps this pane
  // for the batch ledger's whenever the sample is unfocused, and a sort held in
  // the pane's own state went with it. A second mount on the same Pinia is that
  // switch and back.
  it('keeps its sort across a remount', async () => {
    seed(FAMILY_A, FAMILY_B)
    const first = await mountPane()
    first.vm.sortField = 'sample_peak_intensity'
    first.vm.sortOrder = -1
    await first.vm.$nextTick()
    first.unmount()

    const second = await mountPane()
    expect(second.vm.sortField).toBe('sample_peak_intensity')
    expect(second.vm.sortOrder).toBe(-1)
    expect(second.vm.rows[0].sample_peak_intensity).toBe(
      Math.max(...second.vm.rows.map((row) => row.sample_peak_intensity))
    )
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
  // re-sorts the flat array it is given, which is what scattered the isotopologues
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

  // Owner's decision of 2026-11-22: intensity, descending, in both scopes, so
  // the order matches the spectrum; tier is one click away. A, the brighter,
  // leads, though B is the more confident.
  it('opens sorted by intensity, brightest first, the families folded', async () => {
    const wrapper = await mountPane()

    expect(wrapper.vm.sortField).toBe('sample_peak_intensity')
    expect(wrapper.vm.sortOrder).toBe(-1)
    expect(ids(wrapper)).toEqual(['a', 'b'])
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

// The Ion cell (docs/dev/assignment_ledger_and_card.md, section 3.2): the
// mechanism, and a xN pill where the sample's own rows read the same neutral
// through N channels, counted on the run's rows whatever the filters leave.
describe("PaneBrowserAssignment the Ion cell's channel pill", () => {
  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
  })

  const channel = (id, mech, formula, extra = {}) => {
    const fam = family({ id, mz: 100 + id.length, intensity: 100, formula })
    Object.assign(fam.parent, { ionization_mechanism_id: mech, ...extra })
    return fam
  }

  it('counts the rows of the sample that read the neutral, the isotope lines aside', async () => {
    const h = family({ id: 'h', mz: 181.07, intensity: 900, formula: 'C6H12O6', children: [{}] })
    h.parent.ionization_mechanism_id = 'mech-h'
    seed(
      h,
      channel('na', 'mech-na', 'C6H12O6'),
      channel('nh4', 'mech-nh4', 'C6H12O6'),
      channel('lone', 'mech-h', 'C2H6')
    )
    const wrapper = await mountPane()
    const count = (id) =>
      wrapper.vm.channelCount(wrapper.vm.rows.find((r) => r.peak_assignment_id === id))
    expect(count('h')).toBe(3)
    expect(count('na')).toBe(3)
    expect(count('lone')).toBe(1)
  })

  it('counts no source ion and no peak nothing explained', async () => {
    seed(
      channel('r1', 'mech-a', 'NO3', { role: 'reagent' }),
      channel('r2', 'mech-b', 'NO3', { role: 'reagent' }),
      channel('u', 'mech-a', null)
    )
    const wrapper = await mountPane()
    for (const row of wrapper.vm.rows) expect(wrapper.vm.channelCount(row)).toBe(0)
  })

  // Narrowing the ledger leaves the pill as it was: it counts the run's rows.
  it('keeps its count while a filter hides the other channels', async () => {
    seed(
      channel('a', 'mech-a', 'C6H12O6', { tier: 'assigned' }),
      channel('b', 'mech-b', 'C6H12O6', { tier: 'candidate' })
    )
    const wrapper = await mountPane()
    wrapper.vm.toggleTier('assigned')
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.rows.map((r) => r.peak_assignment_id)).toEqual(['a'])
    expect(wrapper.vm.channelCount(wrapper.vm.rows[0])).toBe(2)
  })
})

describe('PaneBrowserAssignment isotopologue labels', () => {
  const ION = 'C9H16O7^N-'

  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
  })

  /** The labelled family, every row naming its ion unless `children` says otherwise. */
  const labelledFamily = (children = [{}, {}]) => {
    const fam = family({
      id: 'n',
      mz: 251.0903,
      intensity: 1000,
      formula: 'C9H16O4',
      children: [
        { sample_peak_mz: 250.0932, isotope_label: 'M-1', isotope_formula: 'C9H16NO7-' },
        { sample_peak_mz: 252.0936, isotope_label: 'M+1', isotope_formula: '[13C][15N]C8H16O7-' }
      ].map((child, index) => ({ ion_formula: ION, ...child, ...children[index] }))
    })
    Object.assign(fam.parent, { ion_formula: ION, isotope_formula: '[15N]C9H16O7-' })
    return fam
  }

  // The shared stubs render no cell, so the rows are passed down to the Column
  // stub, as the corroboration tests do, to read the label the cell shows.
  async function renderedLabels(...families) {
    const tableRows = ref([])
    seed(...families)
    const wrapper = mount(PaneBrowserAssignment, {
      global: {
        directives: { tooltip: {}, help: {} },
        stubs: {
          ...GLOBAL_STUBS,
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
    return wrapper.findAll('.child-label').map((cell) => cell.text())
  }

  it('counts a labelled family from its labelled line, the remainder at 14N', async () => {
    expect(await renderedLabels(labelledFamily())).toEqual(['[14N]', '[13C]'])
  })

  it("reads an isotopologue that names no ion through its M0's", async () => {
    const fam = labelledFamily([{ ion_formula: null }, { ion_formula: undefined }])

    expect(await renderedLabels(fam)).toEqual(['[14N]', '[13C]'])
  })

  // `M+n` only where nothing names the line.
  it('names a line that records no substitution by its offset from the M0', async () => {
    const bare = family({
      id: 'o',
      mz: 100.05,
      intensity: 10,
      formula: 'C2H6',
      children: [{ sample_peak_mz: 102.05, isotope_label: null }]
    })

    expect(await renderedLabels(bare)).toEqual(['M+2'])
  })

  it('keeps the brackets of an unlabelled family', async () => {
    const bromine = family({
      id: 'br',
      mz: 328.6817,
      intensity: 176,
      formula: 'CHBr3',
      children: [
        { sample_peak_mz: 330.6797, isotope_formula: '[81Br]CHBr3-' },
        { sample_peak_mz: 332.6776, isotope_formula: '[81Br]2CHBr2-' }
      ].map((child) => ({ ion_formula: 'CHBr4-', ...child }))
    })

    expect(await renderedLabels(bromine)).toEqual(['[81Br]', '[81Br]2'])
  })
})

// An isotope line with no owner: assigned by hand on a sample whose run holds
// no M0 of its formula (mascope#3661). It has no row to fold under, so it is a
// row of its own, named by its formula with its offset beside it.
describe('PaneBrowserAssignment an isotope line with no M0', () => {
  const LINE = {
    level: '5a',
    peak_assignment_id: 'hand',
    sample_peak_id: 'p-hand',
    sample_peak_mz: 442.867,
    sample_peak_intensity: 40,
    role: 'iso_child',
    source: 'manual',
    tier: 'candidate',
    assigned_formula: 'C6H11Br2O5',
    ion_formula: '[81Br]2C6H11O5-',
    isotope_label: 'M+4',
    isotope_formula: '[81Br]2C6H11O5-',
    owner_peak_assignment_id: null
  }
  const standing = () => ({ parent: { ...LINE }, kids: [] })

  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
  })
  afterEach(() => vi.clearAllMocks())

  it('is a row of its own, folded or not', async () => {
    seed(FAMILY_A, standing())
    const wrapper = await mountPane()
    expect(ids(wrapper)).toContain('hand')
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    expect(ids(wrapper).filter((id) => id === 'hand')).toHaveLength(1)
    const row = wrapper.vm.rows.find((r) => r.peak_assignment_id === 'hand')
    expect(row.isChild).toBe(false)
    expect(row.standingLine).toBe('M+4')
  })

  it('shows its offset beside its formula, and chips that hold no M0', async () => {
    const tableRows = ref([])
    seed(FAMILY_A, standing())
    const wrapper = mount(PaneBrowserAssignment, {
      global: {
        directives: { tooltip: {}, help: {} },
        stubs: {
          ...GLOBAL_STUBS,
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
    expect(wrapper.findAll('[data-testid="standing-line-label"]').map((c) => c.text())).toEqual([
      'M+4'
    ])
    // The standing line's chips are told no role: it holds its own tier and
    // level, not an M0's (the M0 row beside it keeps its own role).
    const chip = (selector) =>
      wrapper.findAll(selector).map((c) => [c.attributes('data-level'), c.attributes('data-role')])
    expect(wrapper.findAll('.tier-tag-stub').map((c) => c.attributes('data-role'))).toEqual([
      'target',
      undefined
    ])
    const levels = chip('.level-tag-stub')
    expect(levels.length).toBeGreaterThan(0)
    expect(levels.every(([level, role]) => level === '5a' && role === undefined)).toBe(true)
  })

  // It reads its formula through the channel it names: no second channel of
  // the M0 row's neutral.
  it('is no channel of its neutral', async () => {
    const m0 = family({ id: 'm', mz: 438.872, intensity: 900, formula: 'C6H11Br2O5' })
    m0.parent.ionization_mechanism_id = 'mech-1'
    seed(m0, { parent: { ...LINE, ionization_mechanism_id: 'mech-1' }, kids: [] })
    const wrapper = await mountPane()
    const rows = Object.fromEntries(wrapper.vm.rows.map((r) => [r.peak_assignment_id, r]))
    expect(wrapper.vm.channelCount(rows.m)).toBe(1)
    expect(wrapper.vm.channelCount(rows.hand)).toBe(0)
  })

  // Group by compound groups a neutral read through several channels. The
  // line and its M0 are one channel, so they make no group.
  it('makes no compound group with its M0', async () => {
    const m0 = family({ id: 'm', mz: 438.872, intensity: 900, formula: 'C6H11Br2O5' })
    m0.parent.ionization_mechanism_id = 'mech-1'
    seed(m0, { parent: { ...LINE, ionization_mechanism_id: 'mech-1' }, kids: [] })
    const wrapper = await mountPane()
    wrapper.vm.view.groupBy = 'compound'
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.rows.filter((r) => r.ledgerKind === 'group')).toEqual([])
    expect(ids(wrapper).sort()).toEqual(['hand', 'm'])
  })

  it('is found by its m/z in the search', async () => {
    seed(FAMILY_A, standing())
    const wrapper = await mountPane()
    wrapper.vm.searchQuery = '442.867'
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['hand'])
  })

  // Its tier and level are its own: it holds no M0's, so the chips are not
  // told it is one's line.
  it("does not read its chips as an M0's line", async () => {
    seed(standing())
    const wrapper = await mountPane()
    const row = wrapper.vm.rows.find((r) => r.peak_assignment_id === 'hand')
    expect(wrapper.vm.chipRoleOf(row)).toBeNull()
  })

  it('an isotopologue that names its M0 still folds under it, unlabelled', async () => {
    seed(FAMILY_A)
    const wrapper = await mountPane()
    expect(ids(wrapper)).toEqual(['a'])
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    const child = wrapper.vm.rows.find((r) => r.peak_assignment_id === 'a-c0')
    expect(child.isChild).toBe(true)
    expect(wrapper.vm.rows.find((r) => r.peak_assignment_id === 'a').standingLine).toBeNull()
    expect(wrapper.vm.chipRoleOf(child)).toBe('iso_child')
  })
})

// The calibrated probability leaves the ledger while its curve is provisional,
// and the tier column says the tiering is still being built (step 3.4d of the
// assignment quality plan). The API still serves the probability on every row,
// so the rows below carry one: the ledger has to leave it out, not merely find
// none.
describe('PaneBrowserAssignment a tiering still in progress', () => {
  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
    const fam = family({ id: 'a', mz: 200.1, intensity: 1000, formula: 'C10H12' })
    fam.parent.p_correct = 0.93
    fam.parent.p_correct_provisional = true
    seed(fam)
  })
  afterEach(() => vi.clearAllMocks())

  // Renders every column's header and cells, so what the ledger shows can be
  // read whole rather than off `rows`.
  async function renderedTable() {
    const tableRows = ref([])
    const wrapper = mount(PaneBrowserAssignment, {
      global: {
        directives: { tooltip: {}, help: {} },
        stubs: {
          ...GLOBAL_STUBS,
          DataTable: {
            ...GLOBAL_STUBS.DataTable,
            watch: {
              value: { handler: (value) => (tableRows.value = value), immediate: true }
            }
          },
          Column: {
            props: ['field', 'header'],
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="stub-col" :data-field="field">' +
              '<div class="stub-head">{{ header }}<slot name="header" /></div>' +
              '<template v-for="(row, i) in rows" :key="i">' +
              '<slot name="body" :data="row" /></template></div>'
          }
        }
      }
    })
    await wrapper.vm.$nextTick()
    return wrapper
  }

  it('shows no calibrated probability', async () => {
    const wrapper = await renderedTable()

    expect(wrapper.text()).not.toContain('P(correct)')
    expect(wrapper.text()).not.toContain('93%')
    expect(wrapper.findAll('.stub-col').map((col) => col.attributes('data-field'))).not.toContain(
      'pCorrect'
    )
    expect(wrapper.vm.rows[0]).not.toHaveProperty('pCorrect')
  })

  it('marks the tier column provisional', async () => {
    const wrapper = await renderedTable()
    const tier = wrapper
      .findAll('.stub-col')
      .find((col) => col.attributes('data-field') === 'tierRank')

    const mark = tier.find('.stub-head [data-testid="tiering-provisional"]')
    expect(mark.exists()).toBe(true)
    expect(mark.text()).toBe(TIERING_PROVISIONAL.label)
    // Only the tier column says it.
    expect(wrapper.findAll('[data-testid="tiering-provisional"]')).toHaveLength(1)
  })

  it('keeps the tier the sort key under the marked header', async () => {
    const wrapper = await renderedTable()

    wrapper.vm.sortField = 'tierRank'
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.rows.map((row) => row.peak_assignment_id)).toEqual(['a'])
  })

  // The channel count is the Ion cell's xN pill, read off the sample's own
  // rows of the neutral.
  it('puts the channel count in the Ion cell', async () => {
    const other = family({ id: 'b', mz: 222.1, intensity: 10, formula: 'C10H12' })
    other.parent.ionization_mechanism_id = 'mech-2'
    seed(family({ id: 'a', mz: 200.1, intensity: 1000, formula: 'C10H12' }), other)
    const wrapper = await renderedTable()
    const ion = wrapper.findAll('.stub-col').find((col) => col.attributes('data-field') === 'mech')

    expect(ion.findAll('[data-testid="channel-pill"]').map((pill) => pill.text())).toEqual([
      '×2',
      '×2'
    ])
  })
})

// What a reference list calls the formula, beside it (step 3.4d): the name,
// the list and its tag, off the flattened field the ledger row carries.
describe('PaneBrowserAssignment reference-list column', () => {
  const LISTING = {
    name: 'decamethylcyclopentasiloxane',
    source: 'cyclic-siloxanes',
    tags: ['background'],
    total: 2
  }

  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
  })
  afterEach(() => vi.clearAllMocks())

  async function renderedListing(...families) {
    return renderedListingAs(false, ...families)
  }

  // Writes each tooltip onto its element, so a test reads what a cell says on hover.
  const recordTooltip = (el, binding) => el.setAttribute('data-tooltip', binding.value ?? '')
  const RECORD_TOOLTIP = { mounted: recordTooltip, updated: recordTooltip }

  async function renderedListingAs(full, ...families) {
    const tableRows = ref([])
    seed(...families)
    if (full) localStorage.setItem('mascope.browser.match.ledgerListing', '{"full":true}')
    const wrapper = mount(PaneBrowserAssignment, {
      global: {
        directives: { tooltip: RECORD_TOOLTIP, help: {} },
        stubs: {
          ...GLOBAL_STUBS,
          DataTable: {
            ...GLOBAL_STUBS.DataTable,
            watch: {
              value: { handler: (value) => (tableRows.value = value), immediate: true }
            }
          },
          Column: {
            props: ['field'],
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="stub-col" :data-field="field"><slot name="header" />' +
              '<template v-for="(row, i) in rows" :key="i">' +
              '<div class="stub-cell"><slot name="body" :data="row" /></div></template></div>'
          }
        }
      }
    })
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    return wrapper
  }

  const listedFamily = () => {
    const fam = family({
      id: 'd5',
      mz: 355.07,
      intensity: 900,
      formula: 'C10H30O5Si5',
      children: [{ sample_peak_mz: 356.07 }]
    })
    fam.parent.reference_listing = LISTING
    return fam
  }
  // The listing is the Compound cell's second line: the Listed as column is
  // gone (docs/dev/assignment_ledger_and_card.md, section 3.2).
  const listingColumn = (wrapper) =>
    wrapper.findAll('.stub-col').find((col) => col.attributes('data-field') === 'assigned_formula')

  it('shows the name, the list and its tag under the formula, in full', async () => {
    const wrapper = await renderedListingAs(true, listedFamily())
    const column = listingColumn(wrapper)
    const cells = column.findAll('.stub-cell')

    const fields = wrapper.findAll('.stub-col').map((col) => col.attributes('data-field'))
    expect(fields).not.toContain('listing')
    expect(column.text()).toContain('Compound')
    expect(column.text()).not.toContain('Listed')

    // The formula first, the listing on the line under it.
    const compound = cells[0].find('[data-testid="compound-cell"]')
    expect(compound.find('.compound-formula .copyable').text()).toBe('C10H30O5Si5')
    const listed = compound.find('.compound-listing')
    expect(listed.attributes('data-testid')).toBe('listed-as')
    expect(listed.find('.listing-name').text()).toBe('decamethylcyclopentasiloxane +1')
    expect(listed.find('.listing-source').text()).toBe('cyclic-siloxanes')
    expect(listed.find('[data-testid="list-tag-background"]').exists()).toBe(true)
    // The isotopologue line under it is the same formula again: its label only.
    expect(cells[1].find('[data-testid="listed-as"]').exists()).toBe(false)
    expect(cells[1].find('[data-testid="compound-cell"]').exists()).toBe(false)
    expect(cells[1].find('.child-label').exists()).toBe(true)
  })

  // The plan owner's notes of 2026-09-26: the ledger was too wide with the
  // column written out. Folded, the name and its list are in the tooltip.
  // No flask: folded, the name is written out and cut short, the list left
  // to the hover.
  it('folds to the name, cut short, and the tags by default, the rest on hover', async () => {
    const wrapper = await renderedListing(listedFamily())
    const column = listingColumn(wrapper)
    const listed = column.findAll('.stub-cell')[0].find('[data-testid="listed-as"]')

    expect(column.find('.ph-flask').exists()).toBe(false)
    expect(listed.find('.listing-source').exists()).toBe(false)
    expect(listed.find('[data-testid="listing-folded"]').text()).toBe(
      'decamethylcyclopentasiloxane +1'
    )
    expect(listed.find('[data-testid="listing-folded"]').classes()).toContain('folded')
    expect(listed.find('[data-testid="list-tag-background"]').exists()).toBe(true)
    // The tooltip carries the name, the list, the tag and the further name.
    const tip = listed.attributes('data-tooltip')
    expect(tip).toContain('decamethylcyclopentasiloxane (cyclic-siloxanes)')
    expect(tip).toContain('and 1 more')
    expect(tip).toContain('tags it background')
  })

  it("writes the column out from the overflow menu's switch, and folds it back", async () => {
    const wrapper = await renderedListing(listedFamily())
    const toggle = wrapper.find('#sample-listing-full')
    const cell = () => listingColumn(wrapper).findAll('.stub-cell')[0]

    expect(toggle.text()).toBe('false')
    await toggle.trigger('click')
    expect(cell().find('.listing-name').text()).toBe('decamethylcyclopentasiloxane +1')
    expect(cell().find('.listing-name').classes()).not.toContain('folded')
    expect(cell().find('.listing-source').exists()).toBe(true)
    // One setting for both ledgers, and kept across a reload.
    expect(JSON.parse(localStorage.getItem('mascope.browser.match.ledgerView')).listingFull).toBe(
      true
    )

    await toggle.trigger('click')
    expect(cell().find('[data-testid="listing-folded"]').exists()).toBe(true)
    expect(cell().find('.listing-source').exists()).toBe(false)
  })

  // A blank empty cell, in both scopes: a dash read as missing data.
  it('leaves a row no list names blank', async () => {
    const wrapper = await renderedListing(FAMILY_B)
    const cell = listingColumn(wrapper).find('.stub-cell')

    expect(cell.find('[data-testid="listed-as"]').exists()).toBe(false)
    expect(cell.find('.compound-formula .copyable').text()).toBe('C2H6')
  })
})

// What a row's target compound declares, beside what a list calls its formula
// (identification claims, section 4.2).
describe("PaneBrowserAssignment the target's candidate", () => {
  const GLUCOSE = 'WQZGKKKJIJFFOK-GASJEMHNSA-N'
  const TARGET = { label: 'target', name: 'glucose', inchikey: GLUCOSE, listed: false }
  const ONLY_TARGET = { name: null, source: null, inchikey: null, tags: [], total: 0 }

  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
  })
  afterEach(() => vi.clearAllMocks())

  const recordTooltip = (el, binding) => el.setAttribute('data-tooltip', binding.value ?? '')
  const RECORD_TOOLTIP = { mounted: recordTooltip, updated: recordTooltip }

  async function rendered(full, ...families) {
    const tableRows = ref([])
    seed(...families)
    if (full) localStorage.setItem('mascope.browser.match.ledgerListing', '{"full":true}')
    const wrapper = mount(PaneBrowserAssignment, {
      global: {
        directives: { tooltip: RECORD_TOOLTIP, help: {} },
        stubs: {
          ...GLOBAL_STUBS,
          DataTable: {
            ...GLOBAL_STUBS.DataTable,
            watch: {
              value: { handler: (value) => (tableRows.value = value), immediate: true }
            }
          },
          Column: {
            props: ['field'],
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="stub-col" :data-field="field"><slot name="header" />' +
              '<template v-for="(row, i) in rows" :key="i">' +
              '<div class="stub-cell"><slot name="body" :data="row" /></div></template></div>'
          }
        }
      }
    })
    await wrapper.vm.$nextTick()
    return wrapper
  }

  const targeted = (listing) => {
    const fam = family({ id: 'glc', mz: 181.07, intensity: 900, formula: 'C6H12O6' })
    fam.parent.reference_listing = listing
    return fam
  }
  const firstCell = (wrapper) =>
    wrapper
      .findAll('.stub-col')
      .find((col) => col.attributes('data-field') === 'assigned_formula')
      .find('.stub-cell [data-testid="listed-as"]')

  afterEach(() => localStorage.clear())

  it('reads a target match as target and its name, unlisted where no list has it', async () => {
    const cell = firstCell(await rendered(true, targeted({ ...ONLY_TARGET, target: TARGET })))

    expect(cell.find('[data-testid="target-as"]').text()).toBe('target')
    expect(cell.find('.listing-name').text()).toBe('glucose')
    expect(cell.find('[data-testid="target-unlisted"]').exists()).toBe(true)
    expect(cell.attributes('data-tooltip')).toContain('what the target stands for')
  })

  it('shows a target a list names by the same structure once, with both labels', async () => {
    const flat = {
      name: 'D-glucose',
      source: 'sugars',
      inchikey: GLUCOSE,
      tags: ['background'],
      total: 2,
      target: { ...TARGET, listed: true }
    }
    const cell = firstCell(await rendered(true, targeted(flat)))

    expect(cell.findAll('.listing-name').map((name) => name.text())).toEqual(['glucose +1'])
    expect(cell.find('.listing-source').text()).toBe('sugars')
    expect(cell.find('[data-testid="list-tag-background"]').exists()).toBe(true)
    expect(cell.find('[data-testid="target-unlisted"]').exists()).toBe(false)
    const tip = cell.attributes('data-tooltip')
    expect(tip).toContain('The sugars list names the same structure')
    expect(tip).toContain('The run matched this formula from a reference list.')
  })

  it("keeps another structure's listing beside the target", async () => {
    const flat = {
      name: 'fructose',
      source: 'sugars',
      inchikey: 'BJHIKXHVCXFQLS-UYFOZJQFSA-N',
      tags: [],
      total: 1,
      target: { ...TARGET, listed: true }
    }
    const cell = firstCell(await rendered(true, targeted(flat)))

    expect(cell.findAll('.listing-name').map((name) => name.text())).toEqual([
      'glucose',
      'fructose'
    ])
  })

  it('folds the target to its mark', async () => {
    const cell = firstCell(await rendered(false, targeted({ ...ONLY_TARGET, target: TARGET })))

    expect(cell.find('.listing-name').exists()).toBe(false)
    expect(cell.find('[data-testid="target-as"]').attributes('aria-label')).toBe('target: glucose')
    expect(cell.find('.ph-flask').exists()).toBe(false)
  })
})

// A run keeps the peak ids it read, and a sample whose peaks were found again
// since has none of them (#2025): the rows still render, off what the run
// stored, but a click on one had nothing to focus and did nothing at all.
describe('PaneBrowserAssignment rows whose peak the sample no longer has', () => {
  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
  })
  afterEach(() => vi.clearAllMocks())

  const livePeaks = (...ids) => ids.map((id) => ({ peak_id: id, mz: 100 }))
  const notice = (wrapper) => wrapper.find('[data-testid="stale-peaks"]')
  const rowClassOf = (wrapper) => wrapper.findComponent({ name: 'DataTable' }).props('rowClass')

  it('says so above the ledger and dims the rows', async () => {
    seed(FAMILY_A, FAMILY_B)
    // B's M0 is current; A's family and B's lines name peaks found before.
    peakList = livePeaks('p-b')
    const wrapper = await mountPane()

    expect(notice(wrapper).text()).toContain(
      "5 rows of this run name a peak the sample's peak list no longer has"
    )
    expect(notice(wrapper).text()).toContain('Run the assignment again')
    const rowClass = rowClassOf(wrapper)
    const rows = new Map(assignmentList.map((row) => [row.peak_assignment_id, row]))
    expect(rowClass(rows.get('a'))).toEqual(['stale-peak'])
    expect(rowClass(rows.get('b'))).toEqual([])
  })

  it('names the whole run when none of its rows resolve', async () => {
    seed(FAMILY_B)
    peakList = livePeaks('p-other')
    const wrapper = await mountPane()

    expect(notice(wrapper).text()).toContain('Every row of this run names a peak')
  })

  it('says nothing while the peaks load, or before any are listed', async () => {
    seed(FAMILY_B)
    peaksPending = true
    peakList = livePeaks('p-other')
    let wrapper = await mountPane()
    expect(notice(wrapper).exists()).toBe(false)
    expect(rowClassOf(wrapper)(assignmentList[0])).toEqual([])

    peaksPending = false
    peakList = []
    wrapper = await mountPane()
    expect(notice(wrapper).exists()).toBe(false)

    // Nor while the run's rows load: the rows on hand are another run's.
    peakList = livePeaks('p-other')
    assignmentsPending = true
    wrapper = await mountPane()
    expect(notice(wrapper).exists()).toBe(false)
  })

  // A failed load keeps the rows the store held before: another sample's peaks,
  // or another run's rows. Neither says anything about this run.
  it('says nothing when the peaks or the run failed to load', async () => {
    seed(FAMILY_B)
    peakList = livePeaks('p-other')
    peaksError = new Error('nope')
    let wrapper = await mountPane()
    expect(notice(wrapper).exists()).toBe(false)
    expect(rowClassOf(wrapper)(assignmentList[0])).toEqual([])

    peaksError = null
    assignmentsError = new Error('nope')
    wrapper = await mountPane()
    expect(notice(wrapper).exists()).toBe(false)
  })

  it('leaves a run whose peaks are all current alone, ids of either type', async () => {
    // The engine stringifies the peak id; the peak store may not, and a row
    // from another source may carry a number. Either way round is one peak.
    const stringRow = family({ id: '7', mz: 120.1, intensity: 50, formula: 'C3H8' })
    stringRow.parent.sample_peak_id = '7'
    const numberRow = family({ id: '8', mz: 130.1, intensity: 40, formula: 'C4H10' })
    numberRow.parent.sample_peak_id = 8
    seed(stringRow, numberRow)
    peakList = [
      { peak_id: 7, mz: 120.1 },
      { peak_id: '8', mz: 130.1 }
    ]
    const wrapper = await mountPane()

    expect(notice(wrapper).exists()).toBe(false)
  })
})

// One row per neutral (step 3.4d, the plan owner's request in the build): a
// neutral committed through more than one channel is one row, headed by its
// strongest reading, with the others under it when the group is opened.
// Show > Group by compound: a group line over each neutral the sample's rows
// read through several channels, the rest under an Ungrouped line
// (docs/dev/assignment_ledger_and_card.md, section 3.5).
describe('PaneBrowserAssignment Group by compound', () => {
  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
  })
  afterEach(() => vi.clearAllMocks())

  /** One reading of a neutral through `mechanism`. */
  const reading = (
    id,
    mechanism,
    { mz, tier = 'assigned', fit = 0.9, formula = 'C10H16O' } = {}
  ) => {
    const fam = family({
      id,
      mz,
      intensity: 100,
      formula,
      tier,
      fit,
      children: [{ sample_peak_mz: mz + 1.003 }]
    })
    fam.parent.ionization_mechanism_id = mechanism
    return fam
  }
  // One neutral through three channels, the proton-transfer reading the
  // strongest; and another neutral through one.
  const protonated = () => reading('h', 'm-h', { mz: 153.127, fit: 0.95 })
  const ammoniated = () => reading('nh4', 'm-nh4', { mz: 170.154, tier: 'candidate', fit: 0.9 })
  const sodiated = () => reading('na', 'm-na', { mz: 175.109, tier: 'candidate', fit: 0.6 })
  const other = () => reading('o', 'm-h', { mz: 137.132, formula: 'C10H16' })

  async function grouped(...families) {
    seed(...families)
    const wrapper = await mountPane()
    wrapper.vm.view.groupBy = 'compound'
    await wrapper.vm.$nextTick()
    return wrapper
  }
  const lines = (wrapper) =>
    wrapper.vm.rows.map((row) =>
      row.ledgerKind === 'group'
        ? `${row.groupName} | ${row.groupCount} | ${row.groupTier}`
        : row.peak_assignment_id
    )

  it('lists a row per ion until it is switched on', async () => {
    seed(protonated(), ammoniated(), other())
    const wrapper = await mountPane()

    expect(wrapper.vm.view.groupBy).toBe('none')
    expect(lines(wrapper).sort()).toEqual(['h', 'nh4', 'o'])
  })

  it("draws a group line over the neutral's channels, the rest Ungrouped", async () => {
    const wrapper = await grouped(ammoniated(), sodiated(), protonated(), other())
    // Equal intensities: confidence orders them, tier first and then fit.
    expect(lines(wrapper)).toEqual([
      'C10H16O | 3 rows | assigned',
      'h',
      'nh4',
      'na',
      'Ungrouped | 1 row | null',
      'o'
    ])
  })

  it('folds a group line and unfolds it again', async () => {
    const wrapper = await grouped(ammoniated(), protonated(), other())
    wrapper.vm.toggleGroupLine(wrapper.vm.rows[0])
    await wrapper.vm.$nextTick()
    expect(lines(wrapper)).toEqual(['C10H16O | 2 rows | assigned', 'Ungrouped | 1 row | null', 'o'])
    expect(wrapper.vm.rows[0].collapsed).toBe(true)
    wrapper.vm.toggleGroupLine(wrapper.vm.rows[0])
    await wrapper.vm.$nextTick()
    expect(lines(wrapper)).toHaveLength(5)
  })

  it("puts each channel's isotopologues under it", async () => {
    const wrapper = await grouped(protonated(), ammoniated())
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    expect(lines(wrapper)).toEqual(['C10H16O | 2 rows | assigned', 'h', 'h-c0', 'nh4', 'nh4-c0'])
  })

  // The group line counts the neutral's rows and names its best tier whatever
  // the filters leave of it, as the pill counts them.
  it('groups what the filters leave, counted on all its rows', async () => {
    const wrapper = await grouped(protonated(), ammoniated(), sodiated())
    wrapper.vm.toggleTier('candidate')
    await wrapper.vm.$nextTick()
    expect(lines(wrapper)).toEqual(['C10H16O | 3 rows | assigned', 'nh4', 'na'])
  })

  it('lists the rows the search keeps, though no group holds one of them', async () => {
    const lone = reading('lone', 'm-h', { mz: 123.117, formula: 'C9H14' })
    const wrapper = await grouped(protonated(), ammoniated(), lone)
    // C9H14 is read through one channel: it is in no group.
    wrapper.vm.searchQuery = 'C9H14'
    await wrapper.vm.$nextTick()
    expect(lines(wrapper)).toEqual(['Ungrouped | 1 row | null', 'lone'])
  })

  it('never groups a row that names no neutral of the sample', async () => {
    const reagent = family({ id: 'br', mz: 78.918, intensity: 9, formula: null, role: 'reagent' })
    reagent.parent.ion_formula = 'Br-'
    const reagent2 = family({ id: 'br2', mz: 80.916, intensity: 9, formula: null, role: 'reagent' })
    reagent2.parent.ion_formula = 'Br-'
    const wrapper = await grouped(reagent, reagent2)

    expect(lines(wrapper).sort()).toEqual(['br', 'br2'])
  })

  it('focuses no peak from a group line', async () => {
    const wrapper = await grouped(protonated(), ammoniated())
    wrapper.vm.selectedRow = wrapper.vm.rows[0]
    expect(peakFocus).not.toHaveBeenCalled()
  })

  it('unfolds every group line again when the grouping changes', async () => {
    const wrapper = await grouped(ammoniated(), protonated(), other())
    wrapper.vm.toggleGroupLine(wrapper.vm.rows[0])
    await wrapper.vm.$nextTick()
    wrapper.vm.view.groupBy = 'none'
    await wrapper.vm.$nextTick()
    wrapper.vm.view.groupBy = 'compound'
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.rows[0].collapsed).toBe(false)
  })

  it("offers compound under the Show menu's Group by, and not series", async () => {
    seed(protonated())
    const wrapper = await mountPane()
    const choice = wrapper.find('.view-menu-popover .group-by')

    expect(choice.attributes('aria-label')).toBe('Group the rows by')
    expect(choice.findAll('button').map((chip) => chip.text())).toEqual(['None', 'Compound'])
    await choice.find('[data-value="compound"]').trigger('click')
    expect(wrapper.vm.view.groupBy).toBe('compound')
    await choice.find('[data-value="none"]').trigger('click')
    expect(wrapper.vm.view.groupBy).toBe('none')
  })
})

// The lens in the sample scope: the sample's list carries no groups, so the
// pill reads the row's detail for the batch's, and falls back to the sample's
// own rows where the batch has no batch peaks yet.
describe('PaneBrowserAssignment the lens', () => {
  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
    mechanismList.splice(
      0,
      mechanismList.length,
      { ionization_mechanism_id: 'm-h', ionization_mechanism: '[M+H]+' },
      { ionization_mechanism_id: 'm-nh4', ionization_mechanism: '[M+NH4]+' }
    )
  })
  afterEach(() => {
    detailRecord = null
    mechanismList.splice(0, mechanismList.length)
    vi.clearAllMocks()
  })

  const channel = (id, mech, { mz, tier = 'assigned', formula = 'C10H16O', intensity = 100 }) => {
    const fam = family({ id, mz, intensity, formula, tier })
    fam.parent.ionization_mechanism_id = mech
    return fam
  }
  const PEAKS = () => [
    channel('h', 'm-h', { mz: 153.127, intensity: 300 }),
    channel('nh4', 'm-nh4', { mz: 170.154, tier: 'candidate', intensity: 200 }),
    channel('o', 'm-h', { mz: 137.132, formula: 'C10H16', intensity: 900 })
  ]
  // The batch's channel group of the neutral: a third channel this sample
  // holds no peak for.
  const SERVED = {
    group_id: 'channels:C10H16O',
    kind: 'channels',
    formula: 'C10H16O',
    members: [
      {
        batch_peak_id: 'bp-h',
        formula: 'C10H16O',
        mechanism: '[M+H]+',
        mz: 153.127,
        sample_peak_id: 'p-h'
      },
      {
        batch_peak_id: 'bp-nh4',
        formula: 'C10H16O',
        mechanism: '[M+NH4]+',
        mz: 170.154,
        sample_peak_id: 'p-nh4'
      },
      {
        batch_peak_id: 'bp-na',
        formula: 'C10H16O',
        mechanism: '[M+Na]+',
        mz: 175.109,
        sample_peak_id: null
      }
    ]
  }
  const ledgerOf = (wrapper) => wrapper.findComponent({ name: 'AssignmentLedger' })
  const labels = (wrapper) =>
    wrapper.vm.rows.map((row) => [row.ledgerKind ?? row.peak_assignment_id, row.lensLabel])
  async function opened(detail) {
    detailRecord = detail
    seed(...PEAKS())
    const wrapper = await mountPane()
    await wrapper.vm.openChannelLens(
      wrapper.vm.rows.find((row) => row.peak_assignment_id === 'nh4')
    )
    await wrapper.vm.$nextTick()
    return wrapper
  }

  it("is built from the row's detail: every member, the sample's own rows and the rest", async () => {
    const wrapper = await opened({ batch_groups: [SERVED] })
    expect(labels(wrapper)).toEqual([
      ['h', '[M+H]+'],
      ['nh4', '[M+NH4]+'],
      ['absent', '[M+Na]+']
    ])
    const absent = wrapper.vm.rows[2]
    expect(absent).toMatchObject({ pseudoNote: 'not in this sample', pseudoMz: 175.109 })
    expect(ledgerOf(wrapper).props('lens')).toEqual({
      title: 'C10H16O through three channels, 3 batch peaks, 1 not in this sample',
      column: 'Channel',
      plot: false
    })
  })

  it("falls back to the sample's own channel rows where the batch has no batch peaks", async () => {
    const wrapper = await opened({ batch_groups: null })
    expect(labels(wrapper)).toEqual([
      ['h', '[M+H]+'],
      ['nh4', '[M+NH4]+']
    ])
    expect(ledgerOf(wrapper).props('lens').title).toBe(
      'C10H16O through two channels, 2 rows of this sample'
    )
  })

  it('opens from the pill on the row', async () => {
    detailRecord = { batch_groups: [SERVED] }
    seed(...PEAKS())
    const tableRows = ref([])
    const wrapper = mount(PaneBrowserAssignment, {
      global: {
        directives: { tooltip: {}, help: {} },
        stubs: {
          ...GLOBAL_STUBS,
          DataTable: {
            ...GLOBAL_STUBS.DataTable,
            watch: {
              value: { handler: (value) => (tableRows.value = value), immediate: true }
            }
          },
          Column: {
            props: ['field'],
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="stub-col" :data-field="field">' +
              '<template v-for="(row, i) in rows" :key="i">' +
              '<div class="stub-cell" :data-row="row.peak_assignment_id"><slot name="body" :data="row" /></div></template></div>'
          }
        }
      }
    })
    await wrapper.vm.$nextTick()
    const pill = wrapper.find('.stub-cell[data-row="h"] [data-testid="channel-pill"]')
    expect(pill.text()).toBe('×2')
    expect(wrapper.find('.stub-cell[data-row="o"] [data-testid="channel-pill"]').exists()).toBe(
      false
    )
    await pill.trigger('click')
    await flushPromisesLocal()
    expect(wrapper.vm.rows.map((row) => row.ledgerKind ?? row.peak_assignment_id)).toEqual([
      'h',
      'nh4',
      'absent'
    ])
    // The member the sample lacks has no plot box: there is no row's species.
    await wrapper.vm.$nextTick()
    const lead = wrapper.findAll('.stub-col').find((col) => col.attributes('data-field') == null)
    expect(lead.find('.stub-cell[data-row="h"] .plot-box').exists()).toBe(true)
    expect(lead.find('.stub-cell[data-row="lens:absent:bp-na"] .plot-box').exists()).toBe(false)
    wrapper.unmount()
  })

  it('lists a series the card opens, its gap and the members the sample lacks apart', async () => {
    const { useLedgerLensStore } = await import('@/lib/ledger/useLedgerLens.js')
    seed(...PEAKS())
    const wrapper = await mountPane()
    useLedgerLensStore().open(
      'sample',
      {
        group_id: 'series:bp-h:CH2:m-h',
        kind: 'series',
        unit: 'CH2',
        root_formula: 'C10H16O',
        members: [
          {
            batch_peak_id: 'bp-h',
            formula: 'C10H16O',
            mz: 153.127,
            step: 0,
            sample_peak_id: 'p-h'
          },
          { batch_peak_id: 'bp-3', formula: 'C13H22O', mz: 195.17, step: 3, sample_peak_id: null }
        ]
      },
      { sampleId: SAMPLE.sample_item_id }
    )
    await wrapper.vm.$nextTick()
    expect(
      wrapper.vm.rows.map((row) => [
        row.ledgerKind ?? row.peak_assignment_id,
        row.lensLabel,
        row.pseudoNote
      ])
    ).toEqual([
      ['h', 'root', undefined],
      ['gap', '+1 CH2', 'no batch peak'],
      ['gap', '+2 CH2', 'no batch peak'],
      ['absent', '+3 CH2', 'not in this sample']
    ])
    expect(ledgerOf(wrapper).props('lens').title).toBe(
      'CH2 series from C10H16O, 2 batch peaks, two steps with no peak, 1 not in this sample'
    )
  })

  it('lifts the tier filter while open, and closes back onto the view', async () => {
    detailRecord = { batch_groups: [SERVED] }
    seed(...PEAKS())
    const wrapper = await mountPane()
    wrapper.vm.toggleTier('assigned')
    await wrapper.vm.$nextTick()
    const before = wrapper.vm.rows.map((row) => row.peak_assignment_id)
    expect(before).not.toContain('nh4')
    await wrapper.vm.openChannelLens(wrapper.vm.rows.find((row) => row.peak_assignment_id === 'h'))
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.rows.map((row) => row.peak_assignment_id)).toContain('nh4')

    ledgerOf(wrapper).vm.$emit('close-lens')
    await wrapper.vm.$nextTick()
    expect(ledgerOf(wrapper).props('lens')).toBeNull()
    expect(wrapper.vm.rows.map((row) => row.peak_assignment_id)).toEqual(before)
  })

  it('survives a sort, the greyed lines after the rows', async () => {
    const wrapper = await opened({ batch_groups: [SERVED] })
    wrapper.vm.sortField = 'sample_peak_intensity'
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()
    expect(labels(wrapper).map(([id]) => id)).toEqual(['nh4', 'h', 'absent'])
  })

  it("is the sample's it was opened on: another sample's lens is not drawn", async () => {
    const { useLedgerLensStore } = await import('@/lib/ledger/useLedgerLens.js')
    seed(...PEAKS())
    const wrapper = await mountPane()
    useLedgerLensStore().open('sample', SERVED, { sampleId: 'si-other' })
    await wrapper.vm.$nextTick()
    expect(ledgerOf(wrapper).props('lens')).toBeNull()
    expect(wrapper.vm.rows.map((row) => row.peak_assignment_id)).toEqual(['o', 'h', 'nh4'])
  })

  it('opens nothing when the sample changed while the detail was read', async () => {
    let answer
    detailRecord = new Promise((resolve) => (answer = resolve))
    seed(...PEAKS())
    const wrapper = await mountPane()
    const opening = wrapper.vm.openChannelLens(wrapper.vm.rows[1])
    wrapper.vm.app.data.sample.focusedId = 'si-2'
    answer({ batch_groups: [SERVED] })
    await opening
    const { useLedgerLensStore } = await import('@/lib/ledger/useLedgerLens.js')
    expect(useLedgerLensStore().lenses.sample).toBeNull()
  })

  // A slow detail read behind one pill, and a later click or close: the
  // reader's last act is what the ledger shows.
  const STALE = { ...SERVED, group_id: 'channels:stale', formula: 'C10H16O (stale)' }
  const slowRead = () => {
    let answer
    detailRecord = new Promise((resolve) => (answer = resolve))
    return (detail) => answer(detail)
  }

  it('opens no lens from a read a newer click overtook', async () => {
    const { useLedgerLensStore } = await import('@/lib/ledger/useLedgerLens.js')
    seed(...PEAKS())
    const wrapper = await mountPane()
    const answer = slowRead()
    const first = wrapper.vm.openChannelLens(
      wrapper.vm.rows.find((row) => row.peak_assignment_id === 'nh4')
    )
    // The second row's detail is at hand: its lens opens at once.
    detailRecord = { batch_groups: [SERVED] }
    await wrapper.vm.openChannelLens(wrapper.vm.rows.find((row) => row.peak_assignment_id === 'h'))
    expect(useLedgerLensStore().lenses.sample.group.group_id).toBe(SERVED.group_id)
    answer({ batch_groups: [STALE] })
    await first
    expect(useLedgerLensStore().lenses.sample.group.group_id).toBe(SERVED.group_id)
  })

  it("opens the last click's lens when two slow reads land out of order", async () => {
    const { useLedgerLensStore } = await import('@/lib/ledger/useLedgerLens.js')
    seed(...PEAKS())
    const wrapper = await mountPane()
    const answerFirst = slowRead()
    const first = wrapper.vm.openChannelLens(
      wrapper.vm.rows.find((row) => row.peak_assignment_id === 'nh4')
    )
    const answerLast = slowRead()
    const last = wrapper.vm.openChannelLens(
      wrapper.vm.rows.find((row) => row.peak_assignment_id === 'h')
    )
    answerFirst({ batch_groups: [STALE] })
    await first
    expect(useLedgerLensStore().lenses.sample).toBeNull()
    answerLast({ batch_groups: [SERVED] })
    await last
    expect(useLedgerLensStore().lenses.sample.group.group_id).toBe(SERVED.group_id)
  })

  it('opens no lens from a read that lands after the reader closed the lens', async () => {
    const { useLedgerLensStore } = await import('@/lib/ledger/useLedgerLens.js')
    const wrapper = await opened({ batch_groups: [SERVED] })
    const answer = slowRead()
    const opening = wrapper.vm.openChannelLens(
      wrapper.vm.rows.find((row) => row.peak_assignment_id === 'h')
    )
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(useLedgerLensStore().lenses.sample).toBeNull()
    answer({ batch_groups: [STALE] })
    await opening
    expect(useLedgerLensStore().lenses.sample).toBeNull()
  })

  it('opens the lens a slow read was for, when nothing came after the click', async () => {
    const { useLedgerLensStore } = await import('@/lib/ledger/useLedgerLens.js')
    seed(...PEAKS())
    const wrapper = await mountPane()
    const answer = slowRead()
    const opening = wrapper.vm.openChannelLens(
      wrapper.vm.rows.find((row) => row.peak_assignment_id === 'nh4')
    )
    answer({ batch_groups: [SERVED] })
    await opening
    expect(useLedgerLensStore().lenses.sample.group.group_id).toBe(SERVED.group_id)
  })

  it('gives back the sort it had before, after a sort picked under it', async () => {
    seed(...PEAKS())
    detailRecord = { batch_groups: [SERVED] }
    const wrapper = await mountPane()
    const before = [wrapper.vm.sortField, wrapper.vm.sortOrder]
    const rowsBefore = wrapper.vm.rows.map((row) => row.peak_assignment_id)
    await wrapper.vm.openChannelLens(wrapper.vm.rows.find((row) => row.peak_assignment_id === 'h'))
    wrapper.vm.sortField = 'sample_peak_mz'
    wrapper.vm.sortOrder = -1
    await wrapper.vm.$nextTick()
    ledgerOf(wrapper).vm.$emit('close-lens')
    await wrapper.vm.$nextTick()
    expect([wrapper.vm.sortField, wrapper.vm.sortOrder]).toEqual(before)
    expect(wrapper.vm.rows.map((row) => row.peak_assignment_id)).toEqual(rowsBefore)
  })

  it('marks no greyed line stale', async () => {
    peakList = [{ peak_id: 'p-h' }, { peak_id: 'p-nh4' }, { peak_id: 'p-o' }]
    try {
      const wrapper = await opened({ batch_groups: [SERVED] })
      const [own, , absent] = wrapper.vm.rows
      expect(wrapper.vm.rowClass(absent)).toBeNull()
      expect(wrapper.vm.rowClass({ ...own, sample_peak_id: 'p-gone' })).toBe('stale-peak')
    } finally {
      peakList = []
    }
  })

  it('focuses no peak from a member the sample lacks', async () => {
    const wrapper = await opened({ batch_groups: [SERVED] })
    wrapper.vm.selectedRow = wrapper.vm.rows[2]
    expect(peakFocus).not.toHaveBeenCalled()
  })
})

describe('PaneBrowserAssignment header and controls', () => {
  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
    seed(FAMILY_A, FAMILY_B)
  })
  afterEach(() => vi.clearAllMocks())

  // The ledger's own breadcrumb is gone: the shell's names the batch and the
  // sample, and its sample chip is the way back out. The count moved into the
  // ledger's header line.
  it('counts the peaks in its header line, and draws no breadcrumb of its own', async () => {
    const wrapper = await mountPane()

    expect(wrapper.find('[data-testid="ledger-count"]').text()).toBe('6 peaks')
    expect(wrapper.vm.breadcrumb).toBeUndefined()
    expect(wrapper.findComponent({ name: 'AssignmentLedger' }).props('label')).toBe('Assignments')
  })

  it('counts nothing with no sample in view', async () => {
    focusedSampleId = ref(null)
    const wrapper = await mountPane()

    expect(wrapper.find('[data-testid="ledger-count"]').exists()).toBe(false)
  })

  // The ledger's rows can show before the sample's peak list does (#3588).
  describe('a row clicked while the peaks load', () => {
    const land = async (list) => {
      peakList = list
      peaksPending = false
      peakRev.value++
      await flushPromises()
    }
    const peakOf = (row) => ({ peak_id: row.sample_peak_id, mz: row.sample_peak_mz })

    beforeEach(() => {
      peaksPending = true
    })

    it('holds the selection, and focuses the peak once the peaks land', async () => {
      const wrapper = await mountPane()
      wrapper.vm.selectedRow = FAMILY_B.parent
      expect(peakFocus).not.toHaveBeenCalled()
      expect(wrapper.vm.selectedRow).toBe(wrapper.vm.rows.find((r) => r.peak_assignment_id === 'b'))

      wrapper.vm.app.ui.tab.active = 'batch'
      await land([peakOf(FAMILY_A.parent), peakOf(FAMILY_B.parent)])
      expect(peakFocus).toHaveBeenCalledTimes(1)
      expect(peakFocus).toHaveBeenCalledWith(peakOf(FAMILY_B.parent))
      // The Sample tab comes forward, as for a click on a row whose peak is in.
      expect(wrapper.vm.app.ui.tab.active).toBe('sample')
    })

    it('drops it when the peaks fail to load', async () => {
      const wrapper = await mountPane()
      wrapper.vm.selectedRow = FAMILY_B.parent

      // A failed load keeps the rows the store held before.
      peaksError = new Error('down')
      await land([peakOf(FAMILY_B.parent)])
      expect(peakFocus).not.toHaveBeenCalled()
    })

    it('drops it when the sample changes first', async () => {
      const wrapper = await mountPane()
      wrapper.vm.selectedRow = FAMILY_B.parent

      focusedSampleId.value = 'si-2'
      await flushPromises()
      expect(wrapper.vm.selectedRow).toBeNull()
      await land([peakOf(FAMILY_B.parent)])
      expect(peakFocus).not.toHaveBeenCalled()
    })

    it('focuses the second of two rows clicked', async () => {
      const wrapper = await mountPane()
      wrapper.vm.selectedRow = FAMILY_A.parent
      wrapper.vm.selectedRow = FAMILY_B.parent

      await land([peakOf(FAMILY_A.parent), peakOf(FAMILY_B.parent)])
      expect(peakFocus).toHaveBeenCalledTimes(1)
      expect(peakFocus).toHaveBeenCalledWith(peakOf(FAMILY_B.parent))
    })

    it('drops it when the row is clicked again, a deselect', async () => {
      const wrapper = await mountPane()
      wrapper.vm.selectedRow = FAMILY_B.parent
      wrapper.vm.selectedRow = null
      expect(wrapper.vm.selectedRow).toBeNull()

      await land([peakOf(FAMILY_B.parent)])
      expect(peakFocus).not.toHaveBeenCalled()
    })

    it("opens the formula search from the row's curate action once the peaks land", async () => {
      const wrapper = await mountPane()
      const { usePeakSearch } = await import('@/lib/panes/PanePeakAssign/stores/peakSearch.js')
      const search = usePeakSearch()
      wrapper.vm.curate(FAMILY_B.parent)
      expect(search.open).toBe(false)

      await land([peakOf(FAMILY_B.parent)])
      expect(peakFocus).toHaveBeenCalledWith(peakOf(FAMILY_B.parent))
      expect(search.open).toBe(true)
    })
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

  // One verdict covers the isotopologue family. An unfolded isotopologue used to
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

    it('shows the compound its verdict on every isotopologue', async () => {
      const wrapper = await unfoldedWithVerdict()
      const rows = rowsById(wrapper)

      expect(wrapper.vm.verdictFor(rows.get('b'))).toEqual(VERDICT)
      expect(wrapper.vm.verdictFor(rows.get('b-c0'))).toEqual(VERDICT)
      expect(wrapper.vm.verdictFor(rows.get('b-c1'))).toEqual(VERDICT)
    })

    it('looks the isotopologue up by its M0 rather than by itself', async () => {
      const wrapper = await unfoldedWithVerdict()
      const rows = rowsById(wrapper)

      wrapper.vm.verdictFor(rows.get('b-c0'))

      // The row handed to the store is the M0 off the assignment list, which is
      // where the record was written; the isotopologue's own identity is not asked
      // about at all.
      expect(forAssignment).toHaveBeenLastCalledWith(byId.get('b'))
    })

    // The next two are regression guards, not proof of the fix - both pass
    // against the pre-fix pane too. The filter runs over parents only and
    // re-attaches each family under whichever parent survived, so a family has
    // always been one unit here. They are worth keeping because the badge column
    // and the filter now have to agree: a filter that started judging isotopologues
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

    // No ownerless-isotopologue case here: `childrenByOwner` skips a null owner, so
    // such a row is neither a parent row nor attached under one and never
    // reaches this table at all. It is reachable in the inspector, and covered
    // there (panePeakAssign.spec.js).
  })

  // The "never two Assign peaks buttons at once" rule now spans two components:
  // the switch bar's copy (AssignmentRunBar) and this pane's empty-state copy.
  // This is the ledger's half - the bar shows one in exactly the states the
  // ledger shows none, asserted in assignmentRunBar.spec.js.
  it('carries an "Assign sample" control only in the empty state', async () => {
    const withRuns = await mountPane()
    expect(withRuns.findAll('[label="Assign sample"]')).toHaveLength(0)

    // No runs, nothing to select: the empty state is the only call to action.
    runList = []
    const withoutRuns = await mountPane()
    expect(withoutRuns.findAll('[label="Assign sample"]')).toHaveLength(1)

    // A run list that failed to load reads nothing like "this sample has none",
    // so it gets the load error rather than the empty state - and the bar keeps
    // the button for it.
    runList = []
    runError = new Error('nope')
    const withError = await mountPane()
    expect(withError.findAll('[label="Assign sample"]')).toHaveLength(0)
  })

  it('opens the shared config dialog from the empty state', async () => {
    runList = []
    const wrapper = await mountPane()

    await wrapper.find('[label="Assign sample"]').trigger('click')

    // The same flag the switch bar's button sets, so whichever button the user
    // reached for opens the one dialog this pane owns.
    expect(useAssignmentLauncher().configVisible).toBe(true)
    expect(wrapper.vm.configVisible).toBe(true)
  })
})

// The isotopologue toggle and the verdict filter used to sit bare in the panel
// header, next to the run selector and the launch button; four controls in a
// column the user can drag to half a window meant the last of them was simply
// clipped off the pane. They are now behind Show, at the end of the filter bar
// - which is only worth anything if they are still reachable and still
// remember their setting.
describe('PaneBrowserAssignment Show menu', () => {
  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
    seed(FAMILY_A, FAMILY_B)
  })
  afterEach(() => vi.clearAllMocks())

  const trigger = (wrapper) => wrapper.find('.view-menu-button')

  const chips = (wrapper) => wrapper.findAll('.view-menu-popover .verdict-filter .menu-chip')

  // Everything that narrows the table is in the filter bar above the tier
  // chips: the search, then Show and the overflow at its end. The panel header
  // keeps only the count.
  it("puts Show at the end of the filter bar, in the cog's place, and nothing in the header", async () => {
    const wrapper = await mountPane()

    expect(wrapper.find('.filter-bar .view-menu-button').attributes('label')).toBe('Show')
    expect(wrapper.find('.filter-bar .overflow-button').exists()).toBe(true)
    expect(wrapper.find('.tier-strip .view-menu-button').exists()).toBe(false)
    expect(wrapper.find('.menu-row').exists()).toBe(false)
    // The run selector and the launch button are a row up now.
    expect(wrapper.find('[label="Assign sample"]').exists()).toBe(false)
    expect(wrapper.find('[placeholder="Select run"]').exists()).toBe(false)
  })

  it('announces itself as a menu button and tracks whether it is open', async () => {
    const wrapper = await mountPane()

    expect(trigger(wrapper).attributes('aria-haspopup')).toBe('dialog')
    expect(trigger(wrapper).attributes('aria-expanded')).toBe('false')
    // The panel is destroyed while closed, so naming it here would be a
    // dangling reference for the whole time the menu is shut.
    expect(trigger(wrapper).attributes('aria-controls')).toBeUndefined()

    await trigger(wrapper).trigger('click')

    expect(trigger(wrapper).attributes('aria-expanded')).toBe('true')
    expect(trigger(wrapper).attributes('aria-controls')).toBe('assignment-view-menu')
  })

  // PrimeVue gives the panel role="dialog" aria-modal="true" and no name, so an
  // unnamed one is announced as just "dialog". The attribute reaches that root
  // because Popover merges fallthrough attrs into it (ptmi).
  it('names the panel it opens', async () => {
    const wrapper = await mountPane()
    const panel = wrapper.find('.view-menu-popover')

    expect(panel.attributes('id')).toBe('assignment-view-menu')
    expect(panel.attributes('aria-label')).toBe('Show')
  })

  // The switch's only accessible name is its `<label for>`; a menu that rendered
  // its items as menuitems would have taken that pairing away. Both ends are
  // asserted - a label pointing at an id nothing carries names nothing, which
  // is exactly what the verdict filter's old label did.
  it('keeps the isotopologue switch labelled by its own text', async () => {
    const wrapper = await mountPane()
    const label = wrapper.find('.view-menu-popover label[for="unfold-iso"]')
    const toggle = wrapper.findComponent({ name: 'ToggleSwitch' })

    expect(label.text()).toBe('Isotopologue lines')
    expect(toggle.props('inputId')).toBe('unfold-iso')
    expect(wrapper.find('.view-menu-popover #unfold-iso').exists()).toBe(true)
  })

  // Without this the panel opens with nothing focused, and being teleported to
  // the end of <body> it is then unreachable by keyboard: PrimeVue's Popover
  // focuses a child only if one carries `[autofocus]`, and there is no fallback
  // to "first focusable". It has to ride on `pt.input` because ToggleSwitch is
  // inheritAttrs:false and puts a fallthrough attribute on its root div, where
  // .focus() is a silent no-op.
  it('marks the switch as the panel autofocus target', async () => {
    const wrapper = await mountPane()

    expect(wrapper.findComponent({ name: 'ToggleSwitch' }).props('pt')).toEqual({
      input: { autofocus: true }
    })
  })

  // Chips, not a Select. PrimeVue's Select calls stopPropagation() on Escape
  // whether or not its list is open, and both of Popover's Escape handlers are
  // on the bubble path - so a Select in here would swallow the only key that
  // closes a panel whose focus trap Tab cannot leave either.
  it('names the verdict filter and each of its choices', async () => {
    const wrapper = await mountPane()
    const group = wrapper.find('.view-menu-popover .verdict-filter')

    expect(group.attributes('role')).toBe('group')
    expect(group.attributes('aria-label')).toBe('Filter by verification verdict')
    expect(chips(wrapper).map((c) => c.text())).toEqual([
      'All verdicts',
      'Confirmed',
      'Rejected',
      'Unsure',
      'Unverified'
    ])
    // Every choice is its own button, so each is a tab stop that names itself.
    expect(chips(wrapper).every((c) => c.element.tagName === 'BUTTON')).toBe(true)
  })

  it('unfolds isotopologues from the menu, not just from the ref', async () => {
    const wrapper = await mountPane()

    await wrapper.find('.view-menu-popover .iso-toggle').trigger('click')

    expect(wrapper.vm.showIsotopologues).toBe(true)
    // The rows change too: the control is wired to the table, not only to a ref
    // that happens to share its name.
    expect(ids(wrapper)).toEqual(['a', 'a-c0', 'a-c1', 'b', 'b-c0', 'b-c1'])
  })

  it('filters by verdict from the menu', async () => {
    verdictRecord = { verdict: 'confirmed', evidence_level: 'msms' }
    verdictPeakId = 'p-b'
    const wrapper = await mountPane()

    const confirmed = chips(wrapper).find((c) => c.text() === 'Confirmed')
    await confirmed.trigger('click')

    expect(wrapper.vm.verdictFilter).toBe('confirmed')
    expect(confirmed.attributes('aria-pressed')).toBe('true')
    expect(ids(wrapper)).toEqual(['b'])
  })

  // The real guarantee is structural: both refs live in the pane's own setup
  // scope, not in the overlay, whose content PrimeVue destroys on every hide.
  // The stub renders its slot unconditionally, so this cycle cannot reproduce
  // that loss - what it does pin is that the rendered controls read the pane's
  // state back rather than holding a copy of their own.
  it('keeps both settings across an open/close cycle', async () => {
    const wrapper = await mountPane()

    await trigger(wrapper).trigger('click')
    await wrapper.find('.view-menu-popover .iso-toggle').trigger('click')
    await chips(wrapper)
      .find((c) => c.text() === 'Unverified')
      .trigger('click')

    // Close, reopen.
    await trigger(wrapper).trigger('click')
    expect(trigger(wrapper).attributes('aria-expanded')).toBe('false')
    await trigger(wrapper).trigger('click')

    expect(wrapper.vm.showIsotopologues).toBe(true)
    expect(wrapper.vm.verdictFilter).toBe('unverified')
    expect(wrapper.find('.view-menu-popover .iso-toggle').text()).toBe('true')
    expect(
      chips(wrapper)
        .filter((c) => c.classes('active'))
        .map((c) => c.text())
    ).toEqual(['Unverified'])
  })

  it('offers no view options before there is a run to look at', async () => {
    runList = []
    const wrapper = await mountPane()

    expect(trigger(wrapper).exists()).toBe(false)
  })
})

// An imported run carries a second tier: the one its producing engine reached on
// its own terms. It sits BESIDE this server's rather than replacing it, because
// `tier` is what the table sorts, filters and rolls up on - and because an
// engine typically tiers only the peaks it committed a formula to, so replacing
// would blank the column on the rest.
describe('PaneBrowserAssignment engine tier column', () => {
  it('is absent from a run with no engine tiers', async () => {
    seed(family({ id: 'a', mz: 100, intensity: 10, formula: 'C6H6' }))
    const wrapper = await mountPane()

    expect(wrapper.vm.hasEngineTiers).toBe(false)
  })

  it('appears once any row carries one', async () => {
    seed(
      family({ id: 'a', mz: 100, intensity: 10, formula: 'C6H6' }),
      family({ id: 'b', mz: 200, intensity: 20, formula: 'C7H8' })
    )
    assignmentList[0].engine_tier = 'candidate'
    const wrapper = await mountPane()

    expect(wrapper.vm.hasEngineTiers).toBe(true)
  })

  // Null, not a rank: `compareBy` sorts blanks last in both directions, which is
  // the right place for "this engine said nothing". tierRank(null) would answer
  // with the 'unassigned' rank and bury such rows at one end of a column they
  // have no opinion in.
  it('ranks a row with no engine tier as null so it sorts last', async () => {
    seed(family({ id: 'a', mz: 100, intensity: 10, formula: 'C6H6' }))
    const wrapper = await mountPane()

    expect(wrapper.vm.rows[0].engineTierRank).toBeNull()
  })

  it('ranks a row that has one', async () => {
    seed(family({ id: 'a', mz: 100, intensity: 10, formula: 'C6H6' }))
    assignmentList[0].engine_tier = 'candidate'
    const wrapper = await mountPane()

    expect(wrapper.vm.rows[0].engineTierRank).toBe(tierRank('candidate'))
  })

  // Two tier chips on one row are a puzzle unless each says which it is.
  it('names both tiers in the chip tooltip when they differ', async () => {
    seed(family({ id: 'a', mz: 100, intensity: 10, formula: 'C6H6' }))
    const wrapper = await mountPane()
    const text = wrapper.vm.engineTierTooltip({ tier: 'assigned', engine_tier: 'candidate' })

    expect(text).toContain('candidate')
    expect(text).toContain('assigned')
  })

  it('says so when they agree', async () => {
    seed(family({ id: 'a', mz: 100, intensity: 10, formula: 'C6H6' }))
    const wrapper = await mountPane()

    expect(wrapper.vm.engineTierTooltip({ tier: 'assigned', engine_tier: 'assigned' })).toContain(
      'agrees'
    )
  })

  it('names both tiers in words, not as the API keys', async () => {
    seed(family({ id: 'a', mz: 100, intensity: 10, formula: 'C6H6' }))
    const wrapper = await mountPane()
    const text = wrapper.vm.engineTierTooltip({
      tier: 'below_assignability',
      engine_tier: 'below_assignability'
    })
    const differs = wrapper.vm.engineTierTooltip({
      tier: 'below_assignability',
      engine_tier: 'candidate'
    })
    // The engine's tier where the two differ, with a name that is not its key.
    const engineDiffers = wrapper.vm.engineTierTooltip({
      tier: 'candidate',
      engine_tier: 'below_assignability'
    })

    expect(text).toContain("own tier: below assignability (agrees with this server's)")
    expect(differs).toContain('banding of the evidence says below assignability')
    expect(engineDiffers).toContain("own tier: below assignability, where this server's")
    expect(`${text}${differs}${engineDiffers}`).not.toContain('below_assignability')
  })
})

// The identification level, in a column of its own beside the tier: what a
// reader may state of the row. Sorted on the scale's rank, filtered by its
// column's menu, and offered only where the run states levels at all.
describe('PaneBrowserAssignment identification level column', () => {
  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
  })
  afterEach(() => vi.clearAllMocks())

  /** A family whose rows carry `level`, the isotopologues their M0's. */
  const leveled = (spec, level, extra = {}) => {
    const fam = family(spec)
    Object.assign(fam.parent, { level, ...extra })
    for (const kid of fam.kids) kid.level = level
    return fam
  }
  const seedLevels = () =>
    seed(
      leveled({ id: 'a', mz: 100.1, intensity: 10, formula: 'C6H6', fit: 0.9 }, '5a', {
        level_evidence: 'rivals not measured',
        level_lift: 'measure the shortlist'
      }),
      leveled({ id: 'b', mz: 200.1, intensity: 20, formula: 'C7H8', fit: 0.8 }, '4a'),
      leveled({ id: 'c', mz: 300.1, intensity: 30, formula: 'C8H10', fit: 0.7 }, null),
      leveled({ id: 'd', mz: 400.1, intensity: 40, formula: 'C9H12', fit: 0.6 }, '4b')
    )

  // Renders each column's header, cells and filter slot, recording the props
  // the pane handed it - the sort field the header click orders by and the one
  // rule its filter menu allows.
  const SelectStub = {
    name: 'Select',
    props: ['modelValue', 'options', 'optionLabel', 'optionValue', 'placeholder'],
    template: '<div class="select-stub" />'
  }
  async function renderedTable({ directives = { tooltip: {}, help: {} } } = {}) {
    const tableRows = ref([])
    const wrapper = mount(PaneBrowserAssignment, {
      global: {
        directives,
        stubs: {
          ...GLOBAL_STUBS,
          Select: SelectStub,
          DataTable: {
            ...GLOBAL_STUBS.DataTable,
            watch: {
              value: { handler: (value) => (tableRows.value = value), immediate: true }
            }
          },
          Column: {
            name: 'Column',
            props: {
              field: String,
              sortField: String,
              sortable: Boolean,
              header: String,
              maxConstraints: Number,
              showAddButton: { type: Boolean, default: null }
            },
            setup: () => ({ rows: tableRows, model: { value: null } }),
            template:
              '<div class="stub-col" :data-field="field">' +
              '<div class="stub-head">{{ header }}<slot name="header" /></div>' +
              '<div class="stub-filter"><slot name="filter" :filterModel="model" ' +
              ':filterCallback="() => {}" /></div>' +
              '<template v-for="(row, i) in rows" :key="i">' +
              '<div class="stub-cell" :data-row="row.peak_assignment_id">' +
              '<slot name="body" :data="row" /></div></template></div>'
          }
        }
      }
    })
    await wrapper.vm.$nextTick()
    return wrapper
  }
  const levelColumn = (wrapper) =>
    wrapper.findAllComponents({ name: 'Column' }).find((col) => col.props('field') === 'level')
  const cell = (wrapper, id) => levelColumn(wrapper).find(`.stub-cell[data-row="${id}"]`)

  it('is absent from a run whose rows carry no level', async () => {
    seed(family({ id: 'a', mz: 100, intensity: 10, formula: 'C6H6' }))
    const wrapper = await renderedTable()

    expect(wrapper.vm.hasLevels).toBe(false)
    expect(levelColumn(wrapper)).toBeUndefined()
  })

  it("appears once any row carries one, sorted on the scale's rank, one filter rule", async () => {
    seedLevels()
    const wrapper = await renderedTable()
    const column = levelColumn(wrapper)

    expect(column).toBeDefined()
    expect(column.props('sortField')).toBe('levelRank')
    expect(column.props('sortable')).toBe(true)
    expect(column.props('maxConstraints')).toBe(1)
    expect(column.props('showAddButton')).toBe(false)
    // Its menu is keyed on the field, where PrimeVue reads the constraint.
    expect(Object.keys(wrapper.vm.filters)).toContain(column.props('field'))
  })

  it('shows the chip with what earned the level and what would lift it, blank where none', async () => {
    seedLevels()
    const wrapper = await renderedTable()

    const chip = cell(wrapper, 'a').find('.level-tag-stub')
    expect(chip.attributes('data-level')).toBe('5a')
    expect(chip.attributes('data-evidence')).toBe('rivals not measured')
    // An older run's words, reworded: no reader can "measure the shortlist".
    expect(chip.attributes('data-lift')).toBe('its rivals measured: this run did not measure them')
    expect(cell(wrapper, 'b').find('.level-tag-stub').attributes('data-level')).toBe('4a')
    expect(cell(wrapper, 'c').find('.level-tag-stub').exists()).toBe(false)
    expect(cell(wrapper, 'c').text()).toBe('')
  })

  it("shows an isotopologue's level as its M0's, marked by its role", async () => {
    seed(
      leveled(
        {
          id: 'a',
          mz: 100.1,
          intensity: 10,
          formula: 'C6H6',
          children: [{ sample_peak_mz: 101.1, sample_peak_intensity: 1 }]
        },
        '4a'
      )
    )
    const wrapper = await renderedTable()
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()

    // A line is a sub-row: its M0's row carries the level, and it repeats
    // none of it.
    expect(cell(wrapper, 'a').find('.level-tag-stub').attributes('data-level')).toBe('4a')
    expect(cell(wrapper, 'a-c0').find('.level-tag-stub').exists()).toBe(false)
  })

  it('ranks a row by the scale, a row with no level null', async () => {
    seedLevels()
    const wrapper = await mountPane()
    const rank = Object.fromEntries(wrapper.vm.rows.map((row) => [row.peak_assignment_id, row]))

    expect(rank.a.levelRank).toBe(LEVELS.indexOf('5a'))
    expect(rank.b.levelRank).toBe(LEVELS.indexOf('4a'))
    expect(rank.c.levelRank).toBeNull()
  })

  it('sorts on the scale, a row with no level last in both directions', async () => {
    seedLevels()
    const wrapper = await mountPane()

    wrapper.vm.sortField = 'levelRank'
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['b', 'd', 'a', 'c'])

    wrapper.vm.sortOrder = -1
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['a', 'd', 'b', 'c'])
  })

  it('leaves the default sort on the intensity', async () => {
    seedLevels()
    const wrapper = await mountPane()

    expect(wrapper.vm.sortField).toBe('sample_peak_intensity')
  })

  it('narrows the rows to the level its menu picks, and back', async () => {
    seedLevels()
    const wrapper = await mountPane()

    wrapper.vm.filters.level.constraints[0].value = '4a'
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['b'])

    wrapper.vm.filters.level.constraints[0].value = null
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toHaveLength(4)
  })

  // Left on with no column to show it, the filter would empty the table for a
  // reason nothing on screen says.
  it('filters nothing where no row carries a level', async () => {
    seed(
      family({ id: 'a', mz: 100, intensity: 10, formula: 'C6H6' }),
      family({ id: 'b', mz: 200, intensity: 20, formula: 'C7H8' })
    )
    const wrapper = await mountPane()

    wrapper.vm.filters.level.constraints[0].value = '4a'
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['b', 'a'])
  })

  it('offers every level in its menu, in the order of the scale', async () => {
    seedLevels()
    const wrapper = await renderedTable()
    const select = levelColumn(wrapper).findComponent({ name: 'Select' })

    expect(select.props('options').map((option) => option.label)).toEqual(LEVELS)
    expect(select.props('optionValue')).toBe('value')
  })

  // The producing engine's own level, an outlined chip beside this server's
  // in the Level cell with Show > Engine readings on, as the batch ledger
  // shows it: an imported run's, or a derived row's from a batch import.
  describe("the engine's level", () => {
    const ledger = (wrapper) => wrapper.findComponent({ name: 'AssignmentLedger' })
    const engineChip = (wrapper, id) => cell(wrapper, id).find('[data-testid="engine-level-chip"]')
    const seedEngineLevels = ({ own = null } = {}) =>
      seed(
        leveled({ id: 'a', mz: 100.1, intensity: 10, formula: 'C6H6' }, own, {
          engine_level: '4b'
        }),
        leveled({ id: 'b', mz: 200.1, intensity: 20, formula: 'C7H8' }, null)
      )

    it('offers the engine readings, and the Level column, for an engine level alone', async () => {
      seedEngineLevels()
      const wrapper = await renderedTable()

      // No level of this server's: the column waits for the engine readings.
      expect(wrapper.vm.hasLevels).toBe(false)
      expect(wrapper.vm.hasEngineReadings).toBe(true)
      expect(levelColumn(wrapper)).toBeUndefined()
      expect(ledger(wrapper).props('engineReadings')).toBe(false)

      wrapper.vm.engineView = true
      await wrapper.vm.$nextTick()
      expect(levelColumn(wrapper)).toBeDefined()
      expect(ledger(wrapper).props('engineReadings')).toBe(true)
      const chip = engineChip(wrapper, 'a')
      expect(chip.attributes('data-level')).toBe('4b')
      expect(chip.attributes('outlined')).toBeDefined()
      // A row the engine stated no level for has no chip, and no own one.
      expect(cell(wrapper, 'b').find('.level-tag-stub').exists()).toBe(false)

      wrapper.vm.engineView = false
      await wrapper.vm.$nextTick()
      expect(levelColumn(wrapper)).toBeUndefined()
    })

    it('is absent from a run that carries no engine level, the view on', async () => {
      seedLevels()
      const wrapper = await renderedTable()
      wrapper.vm.engineView = true
      await wrapper.vm.$nextTick()

      expect(wrapper.vm.hasEngineReadings).toBe(false)
      expect(wrapper.findAll('[data-testid="engine-level-chip"]')).toHaveLength(0)
    })

    it("draws the engine's chip beside this server's level", async () => {
      seedEngineLevels({ own: '5a' })
      const wrapper = await renderedTable()
      // The view off: this server's level alone.
      expect(cell(wrapper, 'a').findAll('.level-tag-stub')).toHaveLength(1)
      expect(engineChip(wrapper, 'a').exists()).toBe(false)

      wrapper.vm.engineView = true
      await wrapper.vm.$nextTick()

      const chips = cell(wrapper, 'a').findAll('.level-tag-stub')
      expect(chips.map((chip) => chip.attributes('data-level'))).toEqual(['5a', '4b'])
      expect(chips[1].attributes('data-testid')).toBe('engine-level-chip')
    })

    it('offers the view in the Show menu for a run with engine levels and no engine tiers', async () => {
      seedEngineLevels()
      const wrapper = await mountPane()

      expect(wrapper.find('label[for="engine-tier-view"]').text()).toBe('Engine tier and level')
    })

    it("names the engine's level, and whether Mascope's agrees", async () => {
      seedEngineLevels()
      const wrapper = await mountPane()
      const tooltip = (level) => wrapper.vm.engineLevelTooltip({ engine_level: '4b', level })

      expect(tooltip(null)).toContain("The producing engine's own level: 4b")
      expect(tooltip(null)).toContain('Mascope states no level of its own for this row')
      expect(tooltip('4b')).toContain("It agrees with Mascope's level")
      expect(tooltip('5a')).toContain("Mascope's level for this row is 5a")
    })
  })

  // The level a reader is shown is the live claim's where a verdict states
  // one, else the engine's (docs/dev/identification_claims.md, section 6.1).
  describe("a live claim's level", () => {
    const CLAIM = {
      sample_peak_id: 'p-b',
      verdict: 'confirmed',
      evidence_level: 'msms',
      level: 2,
      level_letter: 'b',
      claimed_inchikey: 'XLYOFNOQVPJJNP-UHFFFAOYSA-N',
      context: { claim: { evidence: 'two fragments fit one structure', lift: 'a standard' } }
    }
    const chipOf = (wrapper, id) => cell(wrapper, id).find('.level-tag-stub')

    it("shows the claim's level in place of the engine's, with its lines, source claim", async () => {
      verdictPeakId = 'p-b'
      verdictRecord = CLAIM
      seedLevels()
      const wrapper = await renderedTable()

      const chip = chipOf(wrapper, 'b')
      expect(chip.attributes('data-level')).toBe('2b')
      expect(chip.attributes('data-source')).toBe('claim')
      expect(chip.attributes('data-engine-level')).toBe('4a')
      expect(chip.attributes('data-evidence')).toBe('two fragments fit one structure')
      expect(chip.attributes('data-lift')).toBe('a standard')
      // A row no verdict reaches reads the engine's, and says so.
      expect(chipOf(wrapper, 'a').attributes('data-level')).toBe('5a')
      expect(chipOf(wrapper, 'a').attributes('data-source')).toBe('engine')
      expect(chipOf(wrapper, 'a').attributes('data-evidence')).toBe('rivals not measured')
    })

    it("ranks, sorts and filters on the claim's level", async () => {
      // On row a, whose engine level (5a) would sort it after b and d.
      verdictPeakId = 'p-a'
      verdictRecord = { ...CLAIM, sample_peak_id: 'p-a' }
      seedLevels()
      const wrapper = await mountPane()
      const byId = Object.fromEntries(wrapper.vm.rows.map((row) => [row.peak_assignment_id, row]))
      expect(byId.a.levelRank).toBe(LEVELS.indexOf('2b'))

      wrapper.vm.sortField = 'levelRank'
      wrapper.vm.sortOrder = 1
      await wrapper.vm.$nextTick()
      expect(ids(wrapper)).toEqual(['a', 'b', 'd', 'c'])

      wrapper.vm.filters.level.constraints[0].value = '2b'
      await wrapper.vm.$nextTick()
      expect(ids(wrapper)).toEqual(['a'])
      wrapper.vm.filters.level.constraints[0].value = '5a'
      await wrapper.vm.$nextTick()
      expect(ids(wrapper)).toEqual([])
    })

    it('reads a batch-level claim reaching the row as the claim it is', async () => {
      overlayRecord = { ...CLAIM, level: 1, level_letter: null }
      seedLevels()
      const wrapper = await renderedTable()

      expect(chipOf(wrapper, 'b').attributes('data-level')).toBe('1')
      expect(chipOf(wrapper, 'b').attributes('data-source')).toBe('claim')
    })

    // The server serves a level-1 batch claim to a sample its window leaves
    // out with no level and `outside_window`: the verdict reaches the row, its
    // level does not.
    it("reads the engine's level where a batch claim's window leaves the sample out", async () => {
      overlayRecord = {
        ...CLAIM,
        level: null,
        level_letter: null,
        claimed_inchikey: null,
        outside_window: true
      }
      seedLevels()
      const wrapper = await renderedTable()

      expect(chipOf(wrapper, 'b').attributes('data-level')).toBe('4a')
      expect(chipOf(wrapper, 'b').attributes('data-source')).toBe('engine')
    })

    it('offers the column on a run with no engine levels where a claim states one', async () => {
      verdictPeakId = 'p-a'
      verdictRecord = { ...CLAIM, sample_peak_id: 'p-a' }
      seed(family({ id: 'a', mz: 100, intensity: 10, formula: 'C6H6' }))
      const wrapper = await renderedTable()

      expect(wrapper.vm.hasLevels).toBe(true)
      expect(chipOf(wrapper, 'a').attributes('data-level')).toBe('2b')
      expect(chipOf(wrapper, 'a').attributes('data-engine-level')).toBeUndefined()
    })

    it("shows a version-1 claim's bare number, and offers it in the menu", async () => {
      verdictPeakId = 'p-b'
      verdictRecord = { ...CLAIM, level: 3, level_letter: null, context: {} }
      seedLevels()
      const wrapper = await renderedTable()

      expect(chipOf(wrapper, 'b').attributes('data-level')).toBe('3')
      const options = levelColumn(wrapper)
        .findComponent({ name: 'Select' })
        .props('options')
        .map((option) => option.value)
      expect(options).toEqual([...LEVELS.slice(0, 3), '3', ...LEVELS.slice(3)])
    })

    it("reads the M0's claim on an isotopologue line", async () => {
      verdictPeakId = 'p-a'
      verdictRecord = { ...CLAIM, sample_peak_id: 'p-a' }
      seed(
        leveled(
          {
            id: 'a',
            mz: 100.1,
            intensity: 10,
            formula: 'C6H6',
            children: [{ sample_peak_mz: 101.1, sample_peak_intensity: 1 }]
          },
          '4a'
        )
      )
      const wrapper = await mountPane()
      wrapper.vm.showIsotopologues = true
      await wrapper.vm.$nextTick()
      const child = wrapper.vm.rows.find((row) => row.peak_assignment_id === 'a-c0')
      expect(child.shownLevel).toMatchObject({ level: '2b', source: 'claim' })
    })
  })

  it('says what the column is on hover, and opens the level help', async () => {
    seedLevels()
    const record = (name) => (el, binding) =>
      el.setAttribute(name, (name === 'data-help' ? binding.value?.helpKey : binding.value) ?? '')
    const wrapper = await renderedTable({
      directives: {
        tooltip: { mounted: record('data-tooltip') },
        help: { mounted: record('data-help') }
      }
    })
    const header = levelColumn(wrapper).find('.stub-head span')

    expect(header.text()).toBe('Level')
    expect(header.attributes('data-tooltip')).toMatch(/^Identification level/)
    expect(header.attributes('data-tooltip')).toContain('no tier moves on it')
    expect(header.attributes('data-help')).toBe('assignment-level')
  })
})

// An imported run read in its own engine's words: the strip counts, and its
// chips filter, by the engine's tier, while the tier column and the sort stay on
// this server's.
describe('PaneBrowserAssignment engine-tier view', () => {
  const peak = (id, role, tier = 'unassigned') =>
    family({ id, mz: 100 + id.length, intensity: 10, formula: null, tier, fit: null, role })

  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
    seed(
      // The engine holds 'a' a candidate where this server's banding says assigned.
      family({
        id: 'a',
        mz: 150.1,
        intensity: 50,
        formula: 'C6H12O6',
        tier: 'assigned',
        children: [{ sample_peak_mz: 151.1, sample_peak_intensity: 3 }]
      }),
      // A formula the engine stated no tier for.
      family({ id: 'b', mz: 160.1, intensity: 40, formula: 'C7H8', tier: 'below_assignability' }),
      peak('u', 'unassigned'),
      peak('r', 'reagent')
    )
    assignmentList[0].engine_tier = 'candidate'
    tierCountsRecord = {
      assigned: 1,
      candidate: 0,
      below_assignability: 1,
      unassigned: 1,
      reagent: 1,
      artifact: 0
    }
  })

  // The tier chips: the verification queue's chip shares their look, not their job.
  const chips = (wrapper) =>
    wrapper.findAll('.tier-stat:not(.queue-chip)').map((chip) => chip.text())
  const flip = (wrapper) => wrapper.find('.view-menu-popover #engine-tier-view').trigger('click')

  it('is not offered on a run with no engine tiers', async () => {
    assignmentList[0].engine_tier = null
    const wrapper = await mountPane()

    expect(wrapper.find('#engine-tier-view').exists()).toBe(false)
  })

  it("is off by default: the strip counts this server's tiers", async () => {
    const wrapper = await mountPane()

    expect(wrapper.find('label[for="engine-tier-view"]').text()).toBe('Engine tier and level')
    // A chip with nothing in it is not drawn.
    expect(chips(wrapper)).toEqual(['1 assigned', '1 below', '1 unassigned', '1 reagent'])
    expect(wrapper.find('.engine-view-label').exists()).toBe(false)
  })

  it("counts the engine's tiers when on, the formulas it left untiered apart", async () => {
    const wrapper = await mountPane()
    await flip(wrapper)

    // The isotopologue line counts with its compound, not on its own; the
    // reagent keeps its role chip; the unexplained peak stays unassigned.
    expect(chips(wrapper)).toEqual(['1 candidate', '1 unassigned', '1 not stated', '1 reagent'])
    expect(wrapper.find('.engine-view-label').text()).toBe("engine's tiers")
  })

  // A line with no owner is a row of its own and counts under its tier
  // (mascope#3661); a line that names its M0 counts with it.
  it("counts an isotopologue with no owner under the engine's tier", async () => {
    assignmentList.push({
      peak_assignment_id: 'hand',
      sample_peak_id: 'p-hand',
      sample_peak_mz: 170.1,
      role: 'iso_child',
      tier: 'candidate',
      engine_tier: 'candidate',
      assigned_formula: 'C8H10',
      isotope_label: 'M+2',
      owner_peak_assignment_id: null
    })
    const wrapper = await mountPane()
    await flip(wrapper)

    expect(chips(wrapper)).toEqual(['2 candidate', '1 unassigned', '1 not stated', '1 reagent'])
  })

  it("filters by the engine's tier and leaves the sort on this server's", async () => {
    const wrapper = await mountPane()
    await flip(wrapper)

    wrapper.vm.toggleTier('candidate')
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['a'])
    expect(wrapper.vm.rows[0].tierRank).toBe(tierRank('assigned'))

    wrapper.vm.toggleTier('candidate')
    wrapper.vm.toggleTier('engine_unstated')
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['b'])
  })

  // The switch outlives the run it was flipped on; a run whose engine stated no
  // tiers has nothing to read in its words, and no switch to turn it back off.
  it("reads this server's tiers on a run with none of the engine's, whatever the switch", async () => {
    assignmentList[0].engine_tier = null
    const wrapper = await mountPane()
    wrapper.vm.engineView = true
    await wrapper.vm.$nextTick()

    expect(chips(wrapper)).not.toContain('0 not stated')
    expect(chips(wrapper)[0]).toBe('1 assigned')
  })

  it('drops a chip filter when the view changes, since the chip meant the other tier', async () => {
    const wrapper = await mountPane()
    wrapper.vm.toggleTier('assigned')
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['a'])

    await flip(wrapper)

    expect(ids(wrapper).sort()).toEqual(['a', 'b', 'r', 'u'])
  })
})

// The verdict column sorts in both scopes now, on the verdict a row visibly
// carries: its own, else the batch-level one reaching it.
describe('PaneBrowserAssignment verdict column', () => {
  const FAMILY_C = family({
    id: 'c',
    mz: 300.2,
    intensity: 50,
    formula: 'C3H8',
    tier: 'unassigned'
  })
  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
    seed(FAMILY_A, FAMILY_B, FAMILY_C)
    // A confirmed of its own; B rejected at batch level; C unjudged.
    verdictRecord = { verdict: 'confirmed', evidence_level: 'pattern' }
    verdictPeakId = 'p-a'
    overlayRecord = { sample_peak_id: 'p-b', verdict: 'rejected' }
  })

  it('is a sortable column on the rank of the verdict', async () => {
    const wrapper = mount(PaneBrowserAssignment, {
      global: {
        directives: { tooltip: {}, help: {} },
        stubs: {
          ...GLOBAL_STUBS,
          Column: {
            name: 'Column',
            props: { field: String, sortable: Boolean },
            template: '<div />'
          }
        }
      }
    })
    await wrapper.vm.$nextTick()
    const column = wrapper
      .findAllComponents({ name: 'Column' })
      .find((col) => col.props('field') === 'verdictRank')
    expect(column.props('sortable')).toBe(true)
  })

  it('sorts confirmed first and unjudged last, in both directions', async () => {
    const wrapper = await mountPane()
    expect(wrapper.vm.rows.map((row) => row.verdictRank)).toEqual([0, 1, null])

    wrapper.vm.sortField = 'verdictRank'
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['a', 'b', 'c'])

    wrapper.vm.sortOrder = -1
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['b', 'a', 'c'])
  })
})

describe('PaneBrowserAssignment batch-level verdict overlay', () => {
  // The two default families, as the describes above seed them.
  beforeEach(() => {
    seed(FAMILY_A, FAMILY_B)
  })

  /** A batch-level verdict reaching B's M0 (peak `p-b`). */
  const OVERLAY = {
    batch_peak_verification_id: 'bv1',
    batch_peak_id: 'bp-b',
    sample_peak_id: 'p-b',
    assigned_formula: 'C2H6',
    ionization_mechanism_id: null,
    verdict: 'confirmed',
    evidence_level: 'pattern'
  }

  it('shows a batch-level verdict as borrowed on a row with none of its own', async () => {
    overlayRecord = OVERLAY
    const wrapper = await mountPane()

    const rowsById = new Map(wrapper.vm.rows.map((row) => [row.peak_assignment_id, row]))
    expect(wrapper.vm.verdictFor(rowsById.get('b'))).toBeNull()
    expect(wrapper.vm.overlayFor(rowsById.get('b'))).toEqual(OVERLAY)
    expect(wrapper.vm.effectiveVerdictFor(rowsById.get('b'))).toEqual(OVERLAY)
    // A's peak is not the one the verdict reaches.
    expect(wrapper.vm.overlayFor(rowsById.get('a'))).toBeNull()
    // The overlay resolves through the M0 the same way the owned verdict does.
    expect(overlayFor).toHaveBeenCalledWith(rowsById.get('b'))
  })

  it('hands the verdict cell the borrowed verdict, or its own and the disagreement', async () => {
    overlayRecord = OVERLAY
    const wrapper = await mountPane()
    const b = wrapper.vm.rows.find((row) => row.peak_assignment_id === 'b')

    expect(wrapper.vm.verdictCell(b)).toMatchObject({
      record: null,
      overlay: OVERLAY,
      conflict: null,
      label: 'Record a verdict'
    })
    verdictRecord = { verdict: 'rejected', evidence_level: null }
    verdictPeakId = 'p-b'
    expect(wrapper.vm.verdictCell(b)).toMatchObject({
      record: verdictRecord,
      overlay: null,
      conflict: OVERLAY,
      label: 'Verification verdict'
    })
  })

  it('lets the row keep its own verdict over the batch-level one, and names the disagreement', async () => {
    verdictRecord = { verdict: 'rejected', evidence_level: null }
    verdictPeakId = 'p-b'
    overlayRecord = OVERLAY
    const wrapper = await mountPane()

    const rowsById = new Map(wrapper.vm.rows.map((row) => [row.peak_assignment_id, row]))
    expect(wrapper.vm.effectiveVerdictFor(rowsById.get('b'))).toEqual(verdictRecord)
    expect(wrapper.vm.conflictFor(rowsById.get('b'))).toEqual(OVERLAY)
  })

  it('names no disagreement when the two agree', async () => {
    verdictRecord = { verdict: 'confirmed', evidence_level: 'msms' }
    verdictPeakId = 'p-b'
    overlayRecord = OVERLAY
    const wrapper = await mountPane()

    const rowsById = new Map(wrapper.vm.rows.map((row) => [row.peak_assignment_id, row]))
    expect(wrapper.vm.conflictFor(rowsById.get('b'))).toBeNull()
  })

  it('filters on the verdict a row visibly carries, borrowed or not', async () => {
    overlayRecord = OVERLAY
    const wrapper = await mountPane()

    wrapper.vm.verdictFilter = 'confirmed'
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['b'])

    // "Unverified" lists only rows that show no badge at all.
    wrapper.vm.verdictFilter = 'unverified'
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['a'])
  })
})

describe('PaneBrowserAssignment row actions', () => {
  // A completed run, so the table (and with it the verdict popover and the
  // table ref the scroll needs) renders rather than the no-run message.
  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
  })

  it('opens the verdict form for the row the cell was clicked on', async () => {
    seed(FAMILY_A, FAMILY_B)
    const wrapper = await mountPane()
    const row = wrapper.vm.rows.find((r) => r.peak_assignment_id === 'b')

    wrapper.vm.openVerdict({}, row)
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.verdictTarget?.peak_assignment_id).toBe('b')
    expect(wrapper.find('.verdict-popover-stub').text()).toBe('b')
  })

  it('names what the verdict cell offers', async () => {
    seed(FAMILY_A, FAMILY_B)
    const wrapper = await mountPane()
    const row = wrapper.vm.rows.find((r) => r.peak_assignment_id === 'b')
    expect(wrapper.vm.verdictTooltip(row)).toBe('Record a verdict')
  })

  it('cannot plot a row whose peak is not in the batch ledger', async () => {
    seed(FAMILY_A)
    const wrapper = await mountPane()
    const row = wrapper.vm.rows[0]
    expect(wrapper.vm.chart.canPlot(row)).toBe(false)
    expect(wrapper.vm.chart.isPlotted(row)).toBe(false)
  })

  it('scrolls the row of a peak focused elsewhere into view, by its display index', async () => {
    seed(FAMILY_A, FAMILY_B)
    focusedPeak = { peak_id: 'p-b' }
    const wrapper = await mountPane()
    await wrapper.vm.$nextTick()

    const index = wrapper.vm.rows.findIndex((r) => r.sample_peak_id === 'p-b')
    expect(index).toBeGreaterThanOrEqual(0)
    // At the ledger's row height, the scroller's item size.
    expect(scrollVirtualRowIntoView).toHaveBeenCalledWith(expect.anything(), index, {
      itemSize: 40
    })
  })

  it('scrolls nothing while no peak is focused', async () => {
    seed(FAMILY_A, FAMILY_B)
    const wrapper = await mountPane()
    await wrapper.vm.$nextTick()
    expect(scrollVirtualRowIntoView).not.toHaveBeenCalled()
  })

  // The shared stubs never render a cell (DataTable is a bare div, Column is
  // auto-stubbed), so the tests below pass the rows down, as the corroboration
  // tests do, to read the markup the row actions ship. Each Column stub is one
  // `.stub-col`, in template order.
  async function renderedCells(...families) {
    const tableRows = ref([])
    seed(...families)
    const wrapper = mount(PaneBrowserAssignment, {
      global: {
        directives: { tooltip: {}, help: {} },
        stubs: {
          ...GLOBAL_STUBS,
          DataTable: {
            ...GLOBAL_STUBS.DataTable,
            watch: {
              value: { handler: (value) => (tableRows.value = value), immediate: true }
            }
          },
          Column: {
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="stub-col"><template v-for="(row, i) in rows" :key="i">' +
              '<slot name="body" :data="row" /></template></div>'
          }
        }
      }
    })
    await wrapper.vm.$nextTick()
    return wrapper
  }

  // Plot: a checkbox, the same in both scopes (section 3.2).
  it('leads the row with the Plot box, and draws none on a line', async () => {
    const wrapper = await renderedCells(FAMILY_A)
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    const columns = wrapper.findAll('.stub-col')
    expect(columns.length).toBeGreaterThan(1)
    // The M0's box, and none for its two lines.
    expect(columns[0].findAll('.plot-box-stub')).toHaveLength(1)
    expect(columns[0].find('.verdict-cell').exists()).toBe(false)
  })

  it('plots the species from its box, and says whether it is plotted', async () => {
    const wrapper = await renderedCells(FAMILY_A)
    const toggle = vi.spyOn(wrapper.vm.chart, 'toggle').mockImplementation(() => {})
    vi.spyOn(wrapper.vm.chart, 'canPlot').mockReturnValue(true)
    vi.spyOn(wrapper.vm.chart, 'isPlotted').mockReturnValue(true)
    wrapper.vm.$forceUpdate()
    await wrapper.vm.$nextTick()
    const box = wrapper.findAll('.stub-col')[0].find('.plot-box-stub')
    expect(box.attributes('data-checked')).toBe('true')
    expect(box.attributes('aria-label')).toBe('Remove from the batch chart')
    await box.trigger('click')
    expect(toggle).toHaveBeenCalledWith(expect.objectContaining({ peak_assignment_id: 'a' }))
  })

  // `empty` is the pane's empty-state panel class, and its scoped
  // `.empty { height: 220px }` styles every element wearing it: a verdict
  // button called that hands the panel's height to its row.
  it('marks a row with no verdict as unjudged, never as the empty-state panel', async () => {
    const wrapper = await renderedCells(FAMILY_A)
    const cell = wrapper.find('.verdict-cell')
    expect(cell.exists()).toBe(true)
    expect(cell.classes()).toContain('unjudged')
    expect(cell.classes()).not.toContain('empty')
  })

  // A row nobody has judged draws no mark - no faint seal - and its cell is
  // still the button that judges it, named for what it does.
  it('draws nothing in an unjudged verdict cell, and keeps it the button that judges', async () => {
    const wrapper = await renderedCells(FAMILY_A)
    const cell = wrapper.find('.verdict-cell')
    expect(cell.element.tagName).toBe('BUTTON')
    expect(cell.attributes('aria-label')).toBe('Record a verdict')
    expect(cell.find('.pi').exists()).toBe(false)
    expect(cell.find('[class*="ph-"]').exists()).toBe(false)
  })
})

// The source's and the instrument's peaks, each with a chip of its own after the
// tiers. Their rows sit at tier `unassigned`, so without their own buckets they
// were filtered and sorted among the peaks nothing explained.
describe('PaneBrowserAssignment reagent and artifact peaks', () => {
  const peak = (id, role, tier = 'unassigned', fit = null) =>
    family({ id, mz: 100 + id.length, intensity: 10, formula: null, tier, fit, role })

  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
    seed(
      family({ id: 'm', mz: 150.1, intensity: 50, formula: 'C6H12O6', tier: 'assigned' }),
      peak('u', 'unassigned'),
      peak('r', 'reagent'),
      peak('x', 'artifact'),
      peak('rr', 'reagent')
    )
    tierCountsRecord = {
      assigned: 1,
      candidate: 0,
      below_assignability: 0,
      unassigned: 1,
      reagent: 2,
      artifact: 1
    }
  })

  const chips = (wrapper) => wrapper.findAll('.tier-stat:not(.queue-chip)')

  it('counts each role with a chip of its own, after the tiers', async () => {
    const wrapper = await mountPane()

    expect(chips(wrapper).map((chip) => chip.text())).toEqual([
      '1 assigned',
      '1 unassigned',
      '2 reagent',
      '1 artifact'
    ])
    // Set apart from the tiers, in a group of their own.
    expect(
      wrapper.findAll('[data-testid="role-chips"] .tier-stat').map((chip) => chip.text())
    ).toEqual(['2 reagent', '1 artifact'])
  })

  it('filters to one role at a time, and never to the unassigned peaks with them', async () => {
    const wrapper = await mountPane()

    await chips(wrapper)[3].trigger('click')
    expect(ids(wrapper)).toEqual(['x'])

    await chips(wrapper)[3].trigger('click')
    await chips(wrapper)[1].trigger('click')
    expect(ids(wrapper)).toEqual(['u'])

    await chips(wrapper)[2].trigger('click')
    expect(ids(wrapper).sort()).toEqual(['r', 'rr', 'u'])
  })

  // An active chip whose tier has no rows stays, so the filter it holds can be
  // seen and turned off; it is hidden only while off.
  it('keeps an active chip with nothing in it', async () => {
    const wrapper = await mountPane()
    expect(wrapper.find('.tier-stat.candidate').exists()).toBe(false)

    wrapper.vm.toggleTier('candidate')
    await wrapper.vm.$nextTick()
    const chip = wrapper.find('.tier-stat.candidate')
    expect(chip.text()).toBe('0 candidate')
    expect(chip.attributes('aria-pressed')).toBe('true')
    await chip.trigger('click')
    expect(wrapper.find('.tier-stat.candidate').exists()).toBe(false)
  })

  // Show > Roles: off, the ledger lists the sample's compounds and the peaks
  // nothing explained, and the strip drops the roles' chips with their rows.
  it('hides the role rows and their chips from Show', async () => {
    const wrapper = await mountPane()

    await wrapper.find('.view-menu-popover #show-roles').trigger('click')
    expect(ids(wrapper).sort()).toEqual(['m', 'u'])
    expect(chips(wrapper).map((chip) => chip.text())).toEqual(['1 assigned', '1 unassigned'])

    await wrapper.find('.view-menu-popover #show-roles').trigger('click')
    expect(ids(wrapper)).toHaveLength(5)
  })

  it('sorts the role rows after every tier, reagent before artifact', async () => {
    const wrapper = await mountPane()

    const order = ids(wrapper)
    expect(order[0]).toBe('m')
    expect(order[1]).toBe('u')
    expect(order.slice(2, 4).sort()).toEqual(['r', 'rr'])
    expect(order[4]).toBe('x')
  })
})

// A source ion's isotope lines are one ion, as an analyte's are one compound:
// the reagent pass names the ion's monoisotopic row as their owner, and the
// ledger folds them under it. The row names the ion and no compound, so the
// formula column shows the ion.
describe('PaneBrowserAssignment reagent families', () => {
  /** A bromide dimer as the pass writes it: the ion, and two lines it owns. */
  const DIMER = () => {
    const fam = family({
      id: 'br2',
      mz: 157.8367,
      intensity: 2.6e5,
      formula: null,
      tier: 'unassigned',
      fit: null,
      role: 'reagent',
      children: [
        { sample_peak_mz: 159.8347, sample_peak_intensity: 5.0e5, isotope_label: '81Br' },
        { sample_peak_mz: 161.8326, sample_peak_intensity: 2.4e5, isotope_label: '81Br2' }
      ].map((child) => ({
        ...child,
        role: 'reagent',
        tier: 'unassigned',
        assigned_formula: null,
        ionization_mechanism_id: null,
        ion_formula: 'Br2-'
      }))
    })
    fam.parent.ion_formula = 'Br2-'
    return fam
  }
  const GLUCOSE = () =>
    family({ id: 'm', mz: 179.0561, intensity: 1.0e4, formula: 'C6H12O6', tier: 'assigned' })

  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
  })

  /** Mounted with the cells rendered, as the isotopologue label tests do. */
  async function mountWithCells() {
    const tableRows = ref([])
    const wrapper = mount(PaneBrowserAssignment, {
      global: {
        directives: { tooltip: {}, help: {} },
        stubs: {
          ...GLOBAL_STUBS,
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
    await wrapper.vm.$nextTick()
    return wrapper
  }

  it('folds the lines under their ion, counted in its marker', async () => {
    seed(GLUCOSE(), DIMER())
    const wrapper = await mountPane()

    expect(ids(wrapper)).toEqual(['br2', 'm'])
    expect(wrapper.vm.isoCount(wrapper.vm.rows[0])).toBe(2)
  })

  it('lists the lines under their ion when unfolded, in bracket spelling', async () => {
    seed(GLUCOSE(), DIMER())
    const wrapper = await mountWithCells()
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()

    expect(ids(wrapper)).toEqual(['br2', 'br2-c0', 'br2-c1', 'm'])
    expect(familyBreak(wrapper.vm.rows)).toBeNull()
    expect(wrapper.findAll('.child-label').map((cell) => cell.text())).toEqual([
      '[81Br]',
      '[81Br]2'
    ])
  })

  it('selects the ion when one of its folded lines is focused', async () => {
    seed(GLUCOSE(), DIMER())
    focusedPeak = { peak_id: 'p-br2-c0' }
    const wrapper = await mountPane()

    expect(wrapper.vm.selectedRow?.peak_assignment_id).toBe('br2')
  })

  it('shows the ion in the formula column of a row with no compound', async () => {
    seed(GLUCOSE(), DIMER())
    const wrapper = await mountWithCells()

    const cells = wrapper.findAll('.copyable')
    expect(cells.map((cell) => cell.text())).toEqual(['Br2-', 'C6H12O6'])
    expect(cells[1].attributes('data-tooltip')).toBeUndefined()
    expect(cells[0].attributes('data-tooltip')).toMatch(/names an ion, and no compound/)
  })

  it('sorts the ion by what the column shows', async () => {
    seed(
      GLUCOSE(),
      DIMER(),
      family({ id: 'u', mz: 99.1, intensity: 5, formula: null, fit: null, tier: 'unassigned' })
    )
    const wrapper = await mountPane()

    wrapper.vm.sortField = 'assigned_formula'
    wrapper.vm.sortOrder = 1
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['br2', 'm', 'u'])
  })
})

// The verification queue (MAS-329): the chip at the end of the tier strip
// counts the run's queue and narrows the table to it, in the server's order.
describe('PaneBrowserAssignment verification queue', () => {
  const FAMILY_C = family({ id: 'c', mz: 300.2, intensity: 50, formula: 'C5H5', fit: 0.5 })
  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
    seed(FAMILY_A, FAMILY_B, FAMILY_C)
    for (const row of assignmentList) row.evidence = 0.46
    // Against the confidence order (c is assigned, a a candidate): the queue's own.
    queueList = [
      { peak_assignment_id: 'a', reason: 'stale', labels_near: 0, stale: { kind: 'rescored' } },
      { peak_assignment_id: 'c', reason: 'band_edge', near_band: 'assigned', labels_near: 4 }
    ]
  })
  afterEach(() => vi.clearAllMocks())

  const chip = (wrapper) => wrapper.find('[data-testid="queue-chip"]')

  it('counts the queue on a chip of its own at the end of the tier strip', async () => {
    const wrapper = await mountPane()

    expect(chip(wrapper).text()).toBe('2 to verify')
    expect(chip(wrapper).attributes('aria-pressed')).toBe('false')
    // Off: every row, brightest first.
    expect(ids(wrapper)).toEqual(['a', 'b', 'c'])
  })

  it("narrows the table to the queue's rows, in the queue's order", async () => {
    const wrapper = await mountPane()
    await chip(wrapper).trigger('click')

    expect(ids(wrapper)).toEqual(['a', 'c'])
    expect(wrapper.vm.rows.map((row) => row.queueEntry.reason)).toEqual(['stale', 'band_edge'])
    expect(chip(wrapper).attributes('aria-pressed')).toBe('true')

    await chip(wrapper).trigger('click')
    expect(ids(wrapper)).toEqual(['b', 'c', 'a'])
  })

  it('clears a column sort on entering, and keeps the tier chips', async () => {
    const wrapper = await mountPane()
    wrapper.vm.sortField = 'sample_peak_mz'
    wrapper.vm.sortOrder = 1
    await chip(wrapper).trigger('click')

    expect(wrapper.vm.sortField).toBeNull()
    expect(ids(wrapper)).toEqual(['a', 'c'])

    wrapper.vm.toggleTier('candidate')
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['a'])
  })

  it('lets a stale row through whatever the verdict filter says', async () => {
    verdictRecord = { verdict: 'confirmed', evidence_level: 'visual' }
    verdictPeakId = 'p-a'
    const wrapper = await mountPane()
    wrapper.vm.verdictFilter = 'unverified'
    await chip(wrapper).trigger('click')

    expect(ids(wrapper)).toEqual(['a', 'c'])
  })

  // Renders every column's cells, as the provisional-tiering tests do.
  async function renderedTable() {
    const tableRows = ref([])
    const wrapper = mount(PaneBrowserAssignment, {
      global: {
        directives: { tooltip: {}, help: {} },
        stubs: {
          ...GLOBAL_STUBS,
          DataTable: {
            ...GLOBAL_STUBS.DataTable,
            watch: {
              value: { handler: (value) => (tableRows.value = value), immediate: true }
            }
          },
          Column: {
            props: ['field', 'header'],
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="stub-col" :data-field="field">' +
              '<template v-for="(row, i) in rows" :key="i">' +
              '<slot name="body" :data="row" /></template></div>'
          }
        }
      }
    })
    await wrapper.vm.$nextTick()
    return wrapper
  }

  it('says why each row is asked, in a column shown only in the queue', async () => {
    const wrapper = await renderedTable()
    const queueColumn = () =>
      wrapper.findAll('.stub-col').find((col) => col.attributes('data-field') === 'queueRank')
    expect(queueColumn()).toBeUndefined()

    await chip(wrapper).trigger('click')
    expect(queueColumn().find('[data-testid="queue-reason-stale"]').exists()).toBe(true)
    expect(queueColumn().find('[data-testid="queue-reason-band_edge"]').exists()).toBe(true)
  })

  // Guardrail 5: the judgment is of the data. The tier chip's hover gives the
  // evidence in the ledger, and not in the queue.
  it('hides the evidence behind the tier chip while the queue is shown', async () => {
    const wrapper = await renderedTable()
    const evidence = () =>
      wrapper.findAll('.tier-tag-stub').map((tag) => tag.attributes('data-evidence'))
    expect(evidence()).toEqual(['0.46', '0.46', '0.46'])

    await chip(wrapper).trigger('click')
    expect(evidence()).toEqual(['null', 'null'])
  })
})

// The filter bar: the search box over the rows, and every choice of it and of
// Show kept outside the pane, so a visit to the batch ledger and back - which
// unmounts this pane - and a reload find them where they were left.
describe('PaneBrowserAssignment filter bar', () => {
  beforeEach(() => {
    // The run's own mass gate: three fitted widths of 1 ppm about a fitted
    // centre of +1 ppm.
    runList = [
      {
        peak_assignment_run_id: 'run-1',
        status: 'completed',
        sigma_ppm: 1,
        mu_ppm: 1,
        config: { measurement_model: { gate: { cap_z: 3 } } }
      }
    ]
    seed(FAMILY_A, FAMILY_B)
  })
  afterEach(() => vi.clearAllMocks())

  const search = (wrapper, query) => wrapper.find('[data-testid="ledger-search"]').setValue(query)

  // The store keeps a stored sort only on a column it lists for the scope, so
  // every sortable column, the optional ones shown, must be on that list.
  it("lists every sortable column among the store's sort fields", async () => {
    const every = family({
      id: 'e',
      mz: 150.1,
      intensity: 50,
      formula: 'C5H10O5',
      tier: 'assigned'
    })
    every.parent.engine_tier = 'assigned'
    every.parent.level = LEVELS[0]
    seed(every, FAMILY_A)
    const wrapper = mount(PaneBrowserAssignment, {
      global: {
        directives: { tooltip: {}, help: {} },
        stubs: {
          ...GLOBAL_STUBS,
          Column: {
            name: 'Column',
            props: { field: String, sortField: String, sortable: Boolean },
            template: '<div />'
          }
        }
      }
    })
    await wrapper.vm.$nextTick()

    const sorted = wrapper
      .findAllComponents({ name: 'Column' })
      .filter((column) => column.props('sortable'))
      .map((column) => column.props('sortField') ?? column.props('field'))
    // No engine column: the engine's tier is a chip in the Tier cell.
    expect(sorted).not.toContain('engineTierRank')
    expect(sorted).not.toContain('listing')
    expect(sorted).toContain('mech')
    expect(sorted).toContain('levelRank')
    expect(SORT_FIELDS.sample).toEqual(expect.arrayContaining(sorted))
  })

  it('finds a formula, ignoring case', async () => {
    const wrapper = await mountPane()
    await search(wrapper, 'c10')
    expect(ids(wrapper)).toEqual(['a'])
  })

  it('finds a listed name', async () => {
    const listed = family({
      id: 'l',
      mz: 371.1,
      intensity: 50,
      formula: 'C10H30O5Si5',
      tier: 'candidate',
      fit: 0.6
    })
    listed.parent.reference_listing = {
      name: 'decamethylcyclopentasiloxane',
      source: 'cyclic-siloxanes',
      tags: [],
      total: 1
    }
    seed(listed, FAMILY_A)
    const wrapper = await mountPane()
    await search(wrapper, 'cyclopenta')
    expect(ids(wrapper)).toEqual(['l'])
  })

  // A target alone, with no list's name: the target names the row.
  it("finds a target's name", async () => {
    const targeted = family({
      id: 't',
      mz: 181.07,
      intensity: 50,
      formula: 'C6H12O6',
      tier: 'candidate',
      fit: 0.6
    })
    targeted.parent.reference_listing = {
      name: null,
      source: null,
      inchikey: null,
      tags: [],
      total: 0,
      target: { label: 'target', name: 'glucose', inchikey: null, listed: false }
    }
    seed(targeted, FAMILY_A)
    const wrapper = await mountPane()
    await search(wrapper, 'gluc')
    expect(ids(wrapper)).toEqual(['t'])
  })

  // 200.1 measured, +1 ppm off centre: a query 1 ppm under it sits on the
  // centre, and the window is 3 ppm either side of that.
  it("finds an m/z within the run's mass gate, and not outside it", async () => {
    const wrapper = await mountPane()
    const at = (ppm) => (200.1 / (1 + ppm * 1e-6)).toFixed(6)

    await search(wrapper, at(1))
    expect(ids(wrapper)).toEqual(['a'])
    await search(wrapper, at(3.9))
    expect(ids(wrapper)).toEqual(['a'])
    await search(wrapper, at(-1.9))
    expect(ids(wrapper)).toEqual(['a'])
    await search(wrapper, at(4.1))
    expect(ids(wrapper)).toEqual([])
    await search(wrapper, at(-2.1))
    expect(ids(wrapper)).toEqual([])
    // An isotope line's m/z finds the compound it folds under.
    await search(wrapper, '101.05')
    expect(ids(wrapper)).toEqual(['b'])
    // The tooltip says the window.
    expect(wrapper.vm.searchTooltip).toContain('3.0 ppm')
  })

  it("searches at the batch's 5 ppm where the run states no width", async () => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed', sigma_ppm: null }]
    const wrapper = await mountPane()

    await search(wrapper, (200.1 / (1 + 4.9e-6)).toFixed(6))
    expect(ids(wrapper)).toEqual(['a'])
    await search(wrapper, (200.1 / (1 + 5.1e-6)).toFixed(6))
    expect(ids(wrapper)).toEqual([])
  })

  it('keeps every choice across a remount', async () => {
    let wrapper = await mountPane()
    await search(wrapper, 'C')
    wrapper.vm.toggleTier('assigned')
    wrapper.vm.showIsotopologues = true
    wrapper.vm.view.groupBy = 'compound'
    wrapper.vm.verdictFilter = 'unverified'
    wrapper.vm.queueMode = true
    wrapper.vm.engineView = true
    await wrapper.vm.$nextTick()
    wrapper.unmount()

    wrapper = await mountPane()
    expect(wrapper.find('[data-testid="ledger-search"]').element.value).toBe('C')
    // The engine view drops the chips set before it was switched on; the
    // chips set in it stay.
    expect(wrapper.vm.engineView).toBe(true)
    expect(wrapper.vm.showIsotopologues).toBe(true)
    expect(wrapper.vm.view.groupBy).toBe('compound')
    expect(wrapper.vm.verdictFilter).toBe('unverified')
    expect(wrapper.vm.queueMode).toBe(true)
    wrapper.unmount()

    // And a reload.
    setActivePinia(createPinia())
    wrapper = await mountPane()
    expect(wrapper.vm.searchQuery).toBe('C')
    expect(wrapper.vm.view.groupBy).toBe('compound')
    expect(wrapper.vm.queueMode).toBe(true)
  })

  it('keeps the tier chips across a remount', async () => {
    let wrapper = await mountPane()
    wrapper.vm.toggleTier('assigned')
    await wrapper.vm.$nextTick()
    wrapper.unmount()

    wrapper = await mountPane()
    expect([...wrapper.vm.activeTiers]).toEqual(['assigned'])
    expect(ids(wrapper)).toEqual(['b'])
  })

  it('falls back on a stored choice it does not know', async () => {
    localStorage.setItem(
      'mascope.browser.match.ledgerView',
      JSON.stringify({ sample: { groupBy: 'series', verdict: 'maybe', queue: 'on' } })
    )
    setActivePinia(createPinia())
    const wrapper = await mountPane()
    expect(wrapper.vm.view.groupBy).toBe('none')
    expect(wrapper.vm.verdictFilter).toBe('all')
    expect(wrapper.vm.queueMode).toBe(false)
  })
})

// The rows and their actions (docs/dev/assignment_ledger_and_card.md,
// section 3.3), as this scope configures the shared ledger.
describe('PaneBrowserAssignment rows and their actions', () => {
  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
    seed(FAMILY_A, FAMILY_B)
    peakList = assignmentList.map((row) => ({ peak_id: row.sample_peak_id }))
  })
  afterEach(() => vi.clearAllMocks())

  const ledger = (wrapper) => wrapper.findComponent({ name: 'AssignmentLedger' })

  // The rows' cells, rendered through a Column that draws its body per row.
  async function renderedRows() {
    const tableRows = ref([])
    const wrapper = mount(PaneBrowserAssignment, {
      global: {
        directives: { tooltip: {}, help: {} },
        stubs: {
          ...GLOBAL_STUBS,
          DataTable: {
            ...GLOBAL_STUBS.DataTable,
            watch: { value: { handler: (v) => (tableRows.value = v), immediate: true } }
          },
          Column: {
            props: ['field'],
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="stub-col" :data-field="field ?? \'\'">' +
              '<div v-for="(row, i) in rows" :key="i" class="stub-cell" ' +
              ':data-row="row.peak_assignment_id"><slot name="body" :data="row" /></div></div>'
          }
        }
      }
    })
    await wrapper.vm.$nextTick()
    return wrapper
  }
  // The last column of the field: the actions, which carry none, come after
  // the Plot box, which carries none either.
  const cellOf = (wrapper, field, id) =>
    wrapper
      .findAll('.stub-col')
      .filter((col) => col.attributes('data-field') === field)
      .at(-1)
      .find(`.stub-cell[data-row="${id}"]`)

  // Curate: the formula search for the row's peak, beside the inspector.
  it("curates a row in the formula search for its peak, from the row's action", async () => {
    const wrapper = await renderedRows()
    const { usePeakSearch } = await import('@/lib/panes/PanePeakAssign/stores/peakSearch.js')
    const action = cellOf(wrapper, '', 'a').find('.curate-action')
    expect(action.exists()).toBe(true)
    await action.trigger('click')
    expect(usePeakSearch().open).toBe(true)
  })

  it('offers no Curate where there is no formula or no peak to open', async () => {
    seed(FAMILY_B, family({ id: 'u', mz: 99.1, intensity: 5, formula: null, tier: 'unassigned' }))
    peakList = [{ peak_id: 'p-other' }]
    const wrapper = await renderedRows()
    // The peak is gone from the sample's list: a stale row opens nothing.
    expect(cellOf(wrapper, '', 'b').find('.curate-action').exists()).toBe(false)
    expect(cellOf(wrapper, '', 'u').find('.curate-action').exists()).toBe(false)
  })

  it('leaves the search closed when the peak is not in the list', async () => {
    const wrapper = await mountPane()
    const { usePeakSearch } = await import('@/lib/panes/PanePeakAssign/stores/peakSearch.js')
    peakList = []
    wrapper.vm.curate({ sample_peak_id: 'p-none', assigned_formula: 'C2H6' })
    expect(usePeakSearch().open).toBe(false)
  })

  // Clicking an m/z opens the sample's own row in the inspector.
  it("opens the row's peak from its m/z", async () => {
    const wrapper = await mountPane()
    expect(ledger(wrapper).props('mzAction')).toBe('Open this peak in the inspector')
    peakFocus.mockClear()
    ledger(wrapper).vm.$emit('open-mz', wrapper.vm.rows[1])
    expect(peakFocus).toHaveBeenCalledWith({ peak_id: 'p-b' })
  })

  // One family at a time from its caret, against Show's choice.
  it("unfolds one family from its caret, and Show's switch sets them all again", async () => {
    const wrapper = await mountPane()
    const [a, b] = wrapper.vm.rows
    expect(ledger(wrapper).props('familySize')(a)).toBe(2)
    expect(ledger(wrapper).props('familyOpen')(a)).toBe(false)

    ledger(wrapper).vm.$emit('toggle-family', a)
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['a', 'a-c0', 'a-c1', 'b'])
    expect(ledger(wrapper).props('familyOpen')(wrapper.vm.rows[0])).toBe(true)
    // A line has no family of its own.
    expect(ledger(wrapper).props('familySize')(wrapper.vm.rows[1])).toBe(0)

    // Show unfolds every family, and the caret folds one against it.
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['a', 'a-c0', 'a-c1', 'b', 'b-c0', 'b-c1'])
    ledger(wrapper).vm.$emit('toggle-family', b)
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['a', 'a-c0', 'a-c1', 'b'])
  })

  // A sub-row repeats nothing: no box, no verdict.
  it('draws no verdict on a line, its family judged or not', async () => {
    verdictRecord = { verdict: 'confirmed' }
    const wrapper = await renderedRows()
    wrapper.vm.showIsotopologues = true
    await wrapper.vm.$nextTick()
    expect(cellOf(wrapper, 'verdictRank', 'a').find('.verdict-cell').exists()).toBe(true)
    expect(cellOf(wrapper, 'verdictRank', 'a-c0').find('.verdict-cell').exists()).toBe(false)
  })

  it('draws the intensity bars over every row of the run', async () => {
    const wrapper = await mountPane()
    wrapper.vm.toggleTier('assigned')
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).toEqual(['b'])
    expect(ledger(wrapper).props('intensities')).toEqual(
      assignmentList.map((row) => row.sample_peak_intensity)
    )
  })

  // The run's capping reasons, by rule, as a mark on the tier chip.
  it('marks a tier a capping reason holds down, and names the reasons', async () => {
    assignmentList[0].tier_reason_rules = ['evidence_band', 'lone_peak', 'corroborated']
    assignmentList.find((r) => r.peak_assignment_id === 'b').tier_reason_rules = ['corroborated']
    const wrapper = await renderedRows()
    const cap = ledger(wrapper).props('tierCap')
    const rows = Object.fromEntries(wrapper.vm.rows.map((r) => [r.peak_assignment_id, r]))
    expect(cap(rows.a)).toEqual({
      label: 'Held down by: one peak only',
      tooltip:
        'Holds this row at candidate - it cannot be assigned while this stands: one peak only. ' +
        'Open the row for why.'
    })
    expect(cap(rows.b)).toBeNull()
    // The chip is handed it.
    expect(cellOf(wrapper, 'tierRank', 'a').find('.tier-tag-stub').attributes('data-cap')).toBe(
      'Held down by: one peak only'
    )
    expect(
      cellOf(wrapper, 'tierRank', 'b').find('.tier-tag-stub').attributes('data-cap')
    ).toBeUndefined()
  })

  // Show > Engine readings: the engine's tier as an outlined chip, only where
  // the run carries engine tiers.
  it("hands the ledger the engine's readings only while Show asks and the run has them", async () => {
    let wrapper = await mountPane()
    wrapper.vm.engineView = true
    await wrapper.vm.$nextTick()
    expect(ledger(wrapper).props('engineReadings')).toBe(false)

    assignmentList[0].engine_tier = 'candidate'
    wrapper = await mountPane()
    expect(ledger(wrapper).props('engineReadings')).toBe(true)
    wrapper.vm.engineView = false
    await wrapper.vm.$nextTick()
    expect(ledger(wrapper).props('engineReadings')).toBe(false)
  })
})

// The way back to the batch's Batch peaks ledger, in the header line, through
// the real store (stores/batchLedgerReturn.js has the history's own spec).
describe('PaneBrowserAssignment back to Batch peaks', () => {
  const backButton = (wrapper) => wrapper.find('[data-testid="back-to-batch-peaks"]')
  let pushState
  beforeEach(() => {
    window.history.replaceState({ position: 1, current: '/' }, '')
    pushState = vi.spyOn(window.history, 'pushState')
  })
  afterEach(() => {
    vi.restoreAllMocks()
    sampleUnfocus.mockClear()
  })

  it('is offered on a sample of a batch with batch peaks, named as that ledger is', async () => {
    batchPeakList = [{ batch_peak_id: 'bp-1' }]
    const wrapper = await mountPane()
    const button = backButton(wrapper)
    expect(button.exists()).toBe(true)
    expect(button.attributes('label')).toBe('Batch peaks')
    expect(button.attributes('aria-label')).toBe('Back to Batch peaks')
    // In the ledger's header line.
    expect(wrapper.find('.header-end [data-testid="back-to-batch-peaks"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('is not offered while the batch has no batch peaks yet', async () => {
    const wrapper = await mountPane()
    expect(backButton(wrapper).exists()).toBe(false)
    wrapper.unmount()
  })

  it("goes back to the batch's ledger as a step of the browser's history", async () => {
    batchPeakList = [{ batch_peak_id: 'bp-1' }]
    const wrapper = await mountPane()
    await backButton(wrapper).trigger('click')

    expect(sampleUnfocus).toHaveBeenCalledTimes(1)
    expect(pushState).toHaveBeenCalledTimes(1)
    expect(window.history.state.mascopeLedger).toEqual({
      level: 'batch',
      batchId: 'sb-1',
      batchPeakId: null
    })
    wrapper.unmount()
  })
})

// After a hand assignment the run is read again, and the row the click wrote
// can land out of sight: a line folds under its M0, an M0 gains lines. The
// ledger shows it: it opens the family the row is in, keeps the row selected
// and in view, marks a hand line under its M0, and names the filter that
// hides it.
describe('PaneBrowserAssignment after a hand assignment', () => {
  // A family whose first line a person assigned by hand.
  const handFamily = (action = 'set_assignment') => {
    const fam = family({
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
    fam.kids[0] = { ...fam.kids[0], source: 'manual', manual_action: action }
    return fam
  }
  const write = async (wrapper, row, sampleItemId = 'si-1') => {
    writtenRef.value = { sampleItemId, row, seq: (writtenRef.value?.seq ?? 0) + 1 }
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
  }
  const selectedId = (wrapper) => wrapper.vm.selectedRow?.peak_assignment_id ?? null

  beforeEach(() => {
    runList = [{ peak_assignment_run_id: 'run-1', status: 'completed' }]
  })
  afterEach(() => vi.clearAllMocks())

  it("opens a written line's M0 family and selects the line itself", async () => {
    const fam = handFamily()
    seed(fam, FAMILY_B)
    focusedPeak = { peak_id: 'p-a-c0' }
    const wrapper = await mountPane()
    // Folded, the focused line maps to its M0's row.
    expect(selectedId(wrapper)).toBe('a')
    scrollVirtualRowIntoView.mockClear()

    await write(wrapper, fam.kids[0])

    expect(wrapper.vm.familyOpen({ peak_assignment_id: 'a' })).toBe(true)
    expect(wrapper.vm.familyOpen({ peak_assignment_id: 'b' })).toBe(false)
    expect(selectedId(wrapper)).toBe('a-c0')
    // And it is scrolled to, at the line's own index.
    const index = ids(wrapper).indexOf('a-c0')
    expect(index).toBeGreaterThan(0)
    expect(scrollVirtualRowIntoView.mock.calls.at(-1)[1]).toBe(index)
  })

  it("opens an M0's own family once it has lines", async () => {
    seed(FAMILY_A)
    focusedPeak = { peak_id: 'p-a' }
    const wrapper = await mountPane()

    await write(wrapper, FAMILY_A.parent)

    expect(wrapper.vm.familyOpen({ peak_assignment_id: 'a' })).toBe(true)
    expect(ids(wrapper)).toEqual(['a', 'a-c0', 'a-c1'])
    expect(selectedId(wrapper)).toBe('a')
  })

  // Opened, not toggled: a toggle would shut the family the reader had open.
  it('leaves a family that is already open open', async () => {
    const fam = handFamily()
    seed(fam)
    const wrapper = await mountPane()
    wrapper.vm.toggleFamily({ peak_assignment_id: 'a' })
    await wrapper.vm.$nextTick()

    await write(wrapper, fam.kids[0])
    expect(wrapper.vm.familyOpen({ peak_assignment_id: 'a' })).toBe(true)
    await write(wrapper, fam.parent)
    expect(wrapper.vm.familyOpen({ peak_assignment_id: 'a' })).toBe(true)
  })

  it('opens nothing for a row with no family', async () => {
    const lone = family({ id: 'lone', mz: 300.2, intensity: 10, formula: 'C3H8' })
    seed(FAMILY_A, lone)
    const wrapper = await mountPane()
    await write(wrapper, lone.parent)
    expect(wrapper.vm.familyOpen({ peak_assignment_id: 'a' })).toBe(false)
    expect(ids(wrapper).sort()).toEqual(['a', 'lone'])
  })

  // A hand edit moves the row's tier, and with it its place in the sort, while
  // the selection and the row count stay as they were: the row is scrolled to
  // all the same.
  it('scrolls to a written row that opens no family', async () => {
    const lone = family({ id: 'lone', mz: 300.2, intensity: 10, formula: 'C3H8' })
    seed(FAMILY_A, lone)
    focusedPeak = { peak_id: 'p-lone' }
    const wrapper = await mountPane()
    scrollVirtualRowIntoView.mockClear()

    await write(wrapper, lone.parent)

    expect(scrollVirtualRowIntoView).toHaveBeenCalled()
    expect(scrollVirtualRowIntoView.mock.calls.at(-1)[1]).toBe(ids(wrapper).indexOf('lone'))
  })

  it('does nothing for a write on a sample the ledger no longer shows', async () => {
    const fam = handFamily()
    seed(fam)
    const wrapper = await mountPane()
    await write(wrapper, fam.kids[0], 'si-2')
    expect(wrapper.vm.familyOpen({ peak_assignment_id: 'a' })).toBe(false)
  })

  it('unfolds a group line folded over the written row', async () => {
    const m0 = family({ id: 'm', mz: 181.07, intensity: 900, formula: 'C6H12O6' })
    const other = family({ id: 'o', mz: 198.1, intensity: 90, formula: 'C6H12O6' })
    m0.parent.ionization_mechanism_id = 'mech-1'
    other.parent.ionization_mechanism_id = 'mech-2'
    seed(m0, other)
    const wrapper = await mountPane()
    wrapper.vm.view.groupBy = 'compound'
    await wrapper.vm.$nextTick()
    const line = wrapper.vm.rows.find((r) => r.ledgerKind === 'group')
    expect(line).toBeTruthy()
    wrapper.vm.toggleGroupLine(line)
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).not.toContain('m')

    await write(wrapper, m0.parent)
    expect(ids(wrapper)).toContain('m')
  })

  // A row read through one channel is in no compound set: it is listed in
  // the Ungrouped section, whose line folds as well.
  it('unfolds a folded Ungrouped section over the written row', async () => {
    const m0 = family({ id: 'm', mz: 181.07, intensity: 900, formula: 'C6H12O6' })
    const other = family({ id: 'o', mz: 198.1, intensity: 90, formula: 'C6H12O6' })
    const lone = family({ id: 'lone', mz: 300.2, intensity: 10, formula: 'C3H8' })
    m0.parent.ionization_mechanism_id = 'mech-1'
    other.parent.ionization_mechanism_id = 'mech-2'
    seed(m0, other, lone)
    focusedPeak = { peak_id: 'p-lone' }
    const wrapper = await mountPane()
    wrapper.vm.view.groupBy = 'compound'
    await wrapper.vm.$nextTick()
    const line = wrapper.vm.rows.find((r) => r.ledgerKind === 'group' && r.groupKey === 'ungrouped')
    expect(line).toBeTruthy()
    wrapper.vm.toggleGroupLine(line)
    await wrapper.vm.$nextTick()
    expect(ids(wrapper)).not.toContain('lone')

    await write(wrapper, lone.parent)
    expect(ids(wrapper)).toContain('lone')
    expect(selectedId(wrapper)).toBe('lone')
  })

  describe('a hand line under its M0', () => {
    function mountRendered() {
      const tableRows = ref([])
      return mount(PaneBrowserAssignment, {
        global: {
          directives: {
            tooltip: {
              mounted: (el, binding) => (el.dataset.tooltip = binding.value ?? ''),
              updated: (el, binding) => (el.dataset.tooltip = binding.value ?? '')
            },
            help: {}
          },
          stubs: {
            ...GLOBAL_STUBS,
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
    }
    const marks = (wrapper) =>
      wrapper
        .findAll('[data-testid="hand-line-mark"]')
        .map((mark) => [mark.attributes('aria-label'), mark.attributes('data-tooltip')])

    it('wears the hand mark, said of a line a person assigned', async () => {
      seed(handFamily('set_assignment'))
      const wrapper = mountRendered()
      wrapper.vm.showIsotopologues = true
      await wrapper.vm.$nextTick()
      // The one hand line, not its engine-written sibling.
      expect(marks(wrapper)).toEqual([
        ['Assigned by hand', 'Assigned by hand; the next assignment run supersedes this']
      ])
      expect(wrapper.find('[data-testid="hand-line-mark"]').attributes('data-mark')).toBe('manual')
    })

    it('says of a line a hand M0 claimed that its M0 claimed it', async () => {
      seed(handFamily('claim_isotopologue'))
      const wrapper = mountRendered()
      wrapper.vm.showIsotopologues = true
      await wrapper.vm.$nextTick()
      expect(marks(wrapper)).toEqual([
        [
          'Claimed when its M0 was assigned by hand',
          'Claimed when its M0 was assigned by hand; the next assignment run supersedes this'
        ]
      ])
    })

    it('marks no line a run wrote', async () => {
      seed(FAMILY_A)
      const wrapper = mountRendered()
      wrapper.vm.showIsotopologues = true
      await wrapper.vm.$nextTick()
      expect(marks(wrapper)).toEqual([])
    })
  })

  describe('a written row a filter hides', () => {
    const note = (wrapper) => wrapper.find('[data-testid="written-hidden"]')

    it('names the tier chip that hides it, under the filter bar', async () => {
      const fam = handFamily()
      seed(fam, FAMILY_B)
      const wrapper = await mountPane()
      // FAMILY_A is a candidate; only the assigned chip is on.
      wrapper.vm.toggleTier('assigned')
      await wrapper.vm.$nextTick()

      await write(wrapper, fam.kids[0])

      expect(note(wrapper).exists()).toBe(true)
      expect(note(wrapper).text()).toBe(
        'The isotope line you assigned by hand (C10H12 at m/z 201.1000) is hidden by ' +
          'the tier chip on (assigned).'
      )
      // The reader's filter stays as it was.
      expect([...wrapper.vm.activeTiers]).toEqual(['assigned'])

      // Once the row shows the note goes, and a filter set later brings none back.
      wrapper.vm.toggleTier('assigned')
      await wrapper.vm.$nextTick()
      expect(note(wrapper).exists()).toBe(false)
      wrapper.vm.toggleTier('assigned')
      await wrapper.vm.$nextTick()
      expect(note(wrapper).exists()).toBe(false)
    })

    it('names the search that hides it', async () => {
      seed(FAMILY_A, FAMILY_B)
      const wrapper = await mountPane()
      wrapper.vm.searchQuery = 'C2H6'
      await wrapper.vm.$nextTick()
      await write(wrapper, FAMILY_A.parent)
      expect(note(wrapper).text()).toBe(
        'The row you assigned by hand (C10H12 at m/z 200.1000) is hidden by the search "C2H6".'
      )
    })

    it('names Show > Verdict when the verdict filter hides it', async () => {
      seed(FAMILY_A)
      const wrapper = await mountPane()
      wrapper.vm.verdictFilter = 'confirmed'
      await wrapper.vm.$nextTick()
      await write(wrapper, FAMILY_A.parent)
      expect(note(wrapper).text()).toContain('is hidden by Show > Verdict (Confirmed).')
    })

    // The queue ignores Show > Verdict, so a verdict filter left set is not
    // what hides a row the queue leaves out.
    it('names the to verify chip in the verification queue', async () => {
      seed(FAMILY_A)
      const wrapper = await mountPane()
      wrapper.vm.verdictFilter = 'confirmed'
      wrapper.vm.queueMode = true
      await wrapper.vm.$nextTick()
      await write(wrapper, FAMILY_A.parent)
      expect(note(wrapper).text()).toContain('is hidden by the to verify chip.')
    })

    // The queue still applies the tier chips: one of them hiding a queued
    // row is what the note names, not the to verify chip.
    it('names a tier chip that hides a queued row in the verification queue', async () => {
      seed(FAMILY_A)
      queueList = [{ peak_assignment_id: 'a', reason: 'low_margin' }]
      const wrapper = await mountPane()
      wrapper.vm.queueMode = true
      wrapper.vm.toggleTier('assigned')
      await wrapper.vm.$nextTick()
      await write(wrapper, FAMILY_A.parent)
      expect(note(wrapper).text()).toContain('is hidden by the tier chip on (assigned).')
    })

    it('says nothing of a written row the ledger shows', async () => {
      seed(FAMILY_A)
      const wrapper = await mountPane()
      await write(wrapper, FAMILY_A.parent)
      expect(note(wrapper).exists()).toBe(false)
      expect(wrapper.vm.writtenShown).toBeNull()
    })

    it('names Show > Roles when the role rows are hidden', async () => {
      const reagent = family({ id: 'r', mz: 61.99, intensity: 50, formula: null, role: 'reagent' })
      seed(FAMILY_A, reagent)
      const wrapper = await mountPane()
      wrapper.vm.view.roles = false
      await wrapper.vm.$nextTick()
      await write(wrapper, reagent.parent)
      // A row with no formula is named by nothing but what hides it.
      expect(note(wrapper).text()).toBe(
        'The row you assigned by hand is hidden by Show > Roles, which is off.'
      )
    })

    it("names the Level column's filter", async () => {
      const leveled = family({ id: 'l', mz: 250.1, intensity: 30, formula: 'C12H10' })
      leveled.parent.level = '4a'
      FAMILY_B.parent.level = '5a'
      try {
        seed(leveled, FAMILY_B)
        const wrapper = await mountPane()
        wrapper.vm.filters.level.constraints[0].value = '5a'
        await wrapper.vm.$nextTick()
        await write(wrapper, leveled.parent)
        expect(note(wrapper).text()).toContain("is hidden by the Level column's filter.")
      } finally {
        delete FAMILY_B.parent.level
      }
    })

    it('names the lens when one is open on another group', async () => {
      const { useLedgerLensStore } = await import('@/lib/ledger/useLedgerLens.js')
      seed(FAMILY_A, FAMILY_B)
      const wrapper = await mountPane()
      useLedgerLensStore().open(
        'sample',
        {
          group_id: 'channels:C2H6',
          kind: 'channels',
          formula: 'C2H6',
          members: [{ formula: 'C2H6', mechanism: '[M+H]+', mz: 100.05, sample_peak_id: 'p-b' }]
        },
        { sampleId: 'si-1' }
      )
      await wrapper.vm.$nextTick()
      await write(wrapper, FAMILY_A.parent)
      expect(note(wrapper).text()).toContain('is hidden by the lens on a group.')
    })

    it('goes with the sample it was about', async () => {
      seed(FAMILY_A, FAMILY_B)
      const wrapper = await mountPane()
      wrapper.vm.toggleTier('assigned')
      await write(wrapper, FAMILY_A.parent)
      expect(note(wrapper).exists()).toBe(true)
      focusedSampleId.value = 'si-2'
      await wrapper.vm.$nextTick()
      expect(note(wrapper).exists()).toBe(false)
    })

    it('goes when it is closed', async () => {
      seed(FAMILY_A, FAMILY_B)
      const wrapper = await mountPane()
      wrapper.vm.toggleTier('assigned')
      await write(wrapper, FAMILY_A.parent)
      expect(note(wrapper).exists()).toBe(true)
      wrapper.vm.writtenShown = null
      await wrapper.vm.$nextTick()
      expect(note(wrapper).exists()).toBe(false)
    })
  })
})
