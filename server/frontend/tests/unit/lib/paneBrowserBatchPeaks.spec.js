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
        load: vi.fn(),
        // Mirrors the store's own computed, which is what the strip renders.
        tierCounts: peaks.reduce(
          (counts, p) => ({ ...counts, [p.consensus_tier]: (counts[p.consensus_tier] ?? 0) + 1 }),
          { assigned: 0, candidate: 0, below_assignability: 0, unassigned: 0 }
        )
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
// It also carries `processedData`, the filtered-and-sorted view the real table
// exposes and the pane reads to decide what "all" means. A test can narrow it
// to stand in for a column filter, which is the state that separates "every row
// on screen is selected" from "the selection happens to be that large".
const DataTableStub = {
  name: 'DataTable',
  props: ['value', 'filters', 'scrollHeight', 'selection', 'selectAll'],
  emits: ['update:selection', 'select-all-change'],
  data: () => ({ visible: null }),
  computed: {
    processedData() {
      return this.visible ?? this.value
    }
  },
  template: '<div class="datatable"><slot /></div>'
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
    wrapper = await mountPane({
      peaks: [...many(3), peak('other-1', 'candidate', 0.5), peak('other-2', 'candidate', 0.4)]
    })
    const assigned = wrapper.vm.rows.filter((row) => row.consensus_tier === 'assigned')
    const candidates = wrapper.vm.rows.filter((row) => row.consensus_tier === 'candidate')

    // Filter to the assigned rows and select all of them.
    table().vm.visible = assigned
    await wrapper.vm.$nextTick()
    checkSelectAll()
    await wrapper.vm.$nextTick()
    expect(table().props('selectAll')).toBe(true)

    // Now filter to the two candidate rows: the selection is still three rows,
    // larger than what is on screen, and shares none of it.
    table().vm.visible = candidates
    await wrapper.vm.$nextTick()

    expect(app.data.batchPeak.selected).toHaveLength(assigned.length)
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
