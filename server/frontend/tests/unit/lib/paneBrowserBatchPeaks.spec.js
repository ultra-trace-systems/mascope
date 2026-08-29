import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'

import { MAX_SELECTED_BATCH_PEAKS } from '@/stores/data/modules/batchPeak/ledger'

// The batch-peak ledger's menu button launches a background task, so the two
// things it must get right are both about time: it may not offer an action that
// cannot run, and it may not report "done" when all it has is an acknowledgement
// that the work started. The tier strip and the tier column's sort are here too
// - both read the confidence order out of @/lib/tiers, and both used to be
// wrong in the same direction (alphabetical, worst tier first).

const post = vi.fn()

vi.mock('@/api', () => ({ api: { http: { post: (...args) => post(...args) } } }))

let app
// Callbacks the pane registered through app.ui.notification.on, by type.
let notificationHandlers

vi.mock('@/stores', () => ({ useApp: () => app }))

vi.mock('@/lib/base', () => ({
  BaseTabbedPanel: { template: '<div><slot name="menu" /><slot /></div>' },
  BaseTierTag: true,
  BaseCopyableField: true
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

function makeApp({ batch = BATCH, workspace = WORKSPACE, samples = [{}], peaks = [] } = {}) {
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
        load: vi.fn()
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
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template:
    '<button class="iso-toggle" @click="$emit(\'update:modelValue\', !modelValue)">{{ modelValue }}</button>'
}

const GLOBAL_STUBS = {
  Button: {
    props: ['label', 'disabled', 'loading'],
    template:
      '<button class="menu-button" :disabled="disabled" :data-loading="String(!!loading)">{{ label }}</button>'
  },
  Message: { template: '<div class="pane-message"><slot /></div>' },
  DataTable: DataTableStub,
  Column: ColumnStub,
  ToggleSwitch: ToggleSwitchStub,
  InputText: true,
  Select: true
}

// Imported statically (vi.mock is hoisted above it, so the mocks still apply):
// compiling this pane is the expensive part, and as a dynamic import inside the
// first test it lands on that one test's clock.
const { default: PaneBrowserBatchPeaks } =
  await import('@/lib/panes/PaneBrowserMatch/PaneBrowserBatchPeaks.vue')

async function mountPane(options = {}) {
  app = makeApp(options)
  const wrapper = mount(PaneBrowserBatchPeaks, {
    global: {
      stubs: GLOBAL_STUBS,
      directives: { tooltip: {}, help: {} }
    }
  })
  await wrapper.vm.$nextTick()
  return wrapper
}

/** Deliver a `compute_batch_peaks` packet the way the socket would. */
const notify = (payload) =>
  (notificationHandlers['compute_batch_peaks'] ?? []).forEach((cb) => cb(payload))

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

describe('PaneBrowserBatchPeaks compute button applicability', () => {
  it('offers the action when the batch has samples and the user may write', async () => {
    wrapper = await mountPane()

    expect(wrapper.vm.blockedReason).toBeNull()
    expect(wrapper.find('button.menu-button').attributes('disabled')).toBeUndefined()
  })

  it('disables with a reason when no batch is focused', async () => {
    // The pane mounts on "no sample focused" alone, so nothing-focused-at-all
    // renders it - the state the old silent early return hid.
    wrapper = await mountPane({ batch: null })

    expect(wrapper.vm.blockedReason).toMatch(/select a batch/i)
    expect(wrapper.find('button.menu-button').attributes('disabled')).toBeDefined()
  })

  it('disables with a reason when the batch has no samples', async () => {
    wrapper = await mountPane({ samples: [] })

    expect(wrapper.vm.blockedReason).toMatch(/no samples/i)
  })

  it('disables with a reason for a viewer, rather than letting them earn a 403', async () => {
    wrapper = await mountPane({ workspace: { ...WORKSPACE, my_role: 'guest' } })

    expect(wrapper.vm.blockedReason).toMatch(/editor role/i)
  })

  it('stays enabled while the batch samples are still loading', async () => {
    // An empty list is not evidence of an empty batch until the load lands;
    // reading it as one would disable the button on every batch switch.
    wrapper = await mountPane({ samples: [] })
    app.data.sample.pending = true
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.blockedReason).toBeNull()
  })

  it('shows the reason on the wrapper, which a disabled button cannot show itself', async () => {
    wrapper = await mountPane({ batch: null })

    expect(wrapper.find('.compute-button').classes()).toContain('blocked')
    expect(wrapper.vm.computeTooltip).toBe(wrapper.vm.blockedReason)
  })

  it('does not fire the request while blocked', async () => {
    wrapper = await mountPane({ samples: [] })

    await wrapper.vm.computeBatchPeaks()

    expect(post).not.toHaveBeenCalled()
    expect(wrapper.vm.computing).toBe(false)
  })
})

describe('PaneBrowserBatchPeaks compute button progress', () => {
  it('stays loading after the acknowledgement, and stops on the task notification', async () => {
    wrapper = await mountPane()

    await wrapper.vm.computeBatchPeaks()

    // The 202 says the work started, not that it finished.
    expect(post).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.computing).toBe(true)

    notify({ type: 'compute_batch_peaks', status: 'success', process_id: 'proc-1' })
    expect(wrapper.vm.computing).toBe(false)
  })

  it('ignores another backfill of the same batch', async () => {
    // The notification goes to the batch's room, so a second user's run of the
    // same batch arrives here too - and it is not this button's to end.
    wrapper = await mountPane()

    await wrapper.vm.computeBatchPeaks()
    notify({ type: 'compute_batch_peaks', status: 'success', process_id: 'someone-else' })

    expect(wrapper.vm.computing).toBe(true)
  })

  it('stops loading when the task fails, not only when it succeeds', async () => {
    // The reload event fires on success only, so a spinner keyed off that would
    // never stop for a failed run.
    wrapper = await mountPane()

    await wrapper.vm.computeBatchPeaks()
    notify({ type: 'compute_batch_peaks', status: 'error', process_id: 'proc-1' })

    expect(wrapper.vm.computing).toBe(false)
  })

  it('ends on an unidentifiable packet rather than spinning forever', async () => {
    post.mockResolvedValue({ headers: {} })
    wrapper = await mountPane()

    await wrapper.vm.computeBatchPeaks()
    expect(wrapper.vm.computing).toBe(true)

    notify({ type: 'compute_batch_peaks', status: 'success' })
    expect(wrapper.vm.computing).toBe(false)
  })

  it('keeps waiting through the per-sample progress packets', async () => {
    // The backfill reports as it folds each sample, on this same channel and
    // under this same process id. Those packets drive the app's progress bar;
    // the button is asking whether the run is still going, and while they
    // arrive the answer is yes.
    wrapper = await mountPane()

    await wrapper.vm.computeBatchPeaks()

    notify({ type: 'compute_batch_peaks', status: 'pending', process_id: 'proc-1', progress: 25 })
    expect(wrapper.vm.computing).toBe(true)

    notify({ type: 'compute_batch_peaks', status: 'pending', process_id: 'proc-1', progress: 75 })
    expect(wrapper.vm.computing).toBe(true)

    // ...and the terminal packet still ends it.
    notify({ type: 'compute_batch_peaks', status: 'success', process_id: 'proc-1' })
    expect(wrapper.vm.computing).toBe(false)
  })

  it('gives up after the timeout, so a dropped socket cannot strand the button', async () => {
    wrapper = await mountPane()

    await wrapper.vm.computeBatchPeaks()
    expect(wrapper.vm.computing).toBe(true)

    vi.advanceTimersByTime(5 * 60 * 1000)
    expect(wrapper.vm.computing).toBe(false)
  })

  it('resets when the focused batch changes', async () => {
    wrapper = await mountPane()
    await wrapper.vm.computeBatchPeaks()

    app.data.batch.focusedId = 'b-2'
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.computing).toBe(false)
  })
})

describe('PaneBrowserBatchPeaks failed launch', () => {
  it('resets the button and shows the refusal once', async () => {
    // 403 is the refusal this route issues - the editor-role check and the
    // feature flag both answer with one - and what an editor role revoked
    // mid-session looks like from here.
    post.mockRejectedValue(
      apiError(403, 'Access denied. You do not have permission to perform this action.')
    )
    wrapper = await mountPane()

    await wrapper.vm.computeBatchPeaks()
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.computing).toBe(false)
    expect(wrapper.vm.launchRefused).toBe(true)
    expect(wrapper.findAll('.pane-message')).toHaveLength(1)
    expect(wrapper.find('.pane-message').text()).toContain('do not have permission')
  })

  it('distinguishes a genuine failure from a refusal', async () => {
    post.mockRejectedValue(apiError(500, 'Unexpected error (ref: abc12345).'))
    wrapper = await mountPane()

    await wrapper.vm.computeBatchPeaks()

    expect(wrapper.vm.launchRefused).toBe(false)
    expect(wrapper.vm.launchError).toContain('Unexpected error')
  })

  it('asks the interceptor to hold the toast it would duplicate', async () => {
    // The message is rendered in the pane, so the global error toast would say
    // the same thing twice. `errors: 'inline'` is what holds it back
    // (src/api/http.js); the interceptor itself is mocked out here, so the
    // option being sent is the whole of what this test can hold.
    post.mockRejectedValue(apiError(500, 'boom'))
    wrapper = await mountPane()

    await wrapper.vm.computeBatchPeaks()

    expect(post.mock.calls[0][2]).toMatchObject({ errors: 'inline' })
  })

  it('clears a previous failure when the next attempt starts', async () => {
    post.mockRejectedValueOnce(apiError(500, 'boom')).mockResolvedValue(ack())
    wrapper = await mountPane()

    await wrapper.vm.computeBatchPeaks()
    expect(wrapper.vm.launchError).toBe('boom')

    await wrapper.vm.computeBatchPeaks()
    expect(wrapper.vm.launchError).toBeNull()
  })
})

describe('PaneBrowserBatchPeaks tier strip', () => {
  const peaks = [
    peak('bp-1', 'assigned', 0.9),
    peak('bp-2', 'assigned', 0.7),
    peak('bp-3', 'candidate', 0.5),
    peak('bp-4', 'unassigned', null)
  ]

  it('renders one chip per tier in confidence order, with counts', async () => {
    wrapper = await mountPane({ peaks })
    const chips = wrapper.findAll('.tier-stat')

    expect(chips.map((chip) => chip.text())).toEqual([
      '2 assigned',
      '1 candidate',
      '0 below',
      '1 unassigned'
    ])
  })

  it('has no reagent chip - a batch peak carries no role to put in one', async () => {
    wrapper = await mountPane({ peaks })

    expect(wrapper.text()).not.toContain('reagent')
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

  it('is toggled from the pane menu', async () => {
    wrapper = await mountPane({ peaks: [M0, ISO] })

    await wrapper.find('.iso-toggle').trigger('click')

    expect(wrapper.vm.showIsotopologues).toBe(true)
    expect(wrapper.vm.rows).toHaveLength(2)
  })
})
