import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// Assign batch: one control that says where the batch stands and runs what it
// needs. Everything it shows comes from the server's status read, so each row
// of the brief's table is a status fixture here; what it runs is a POST whose
// body the tests read; and a pass is followed by its process id, so another
// process's packets must end nothing.

const post = vi.fn()
const get = vi.fn()
// Socket listeners the store registered, by event.
let socketHandlers

vi.mock('@/api', () => ({
  api: {
    http: { post: (...args) => post(...args), get: (...args) => get(...args) },
    socket: {
      on: (event, callback) => {
        socketHandlers[event] = [...(socketHandlers[event] ?? []), callback]
      }
    }
  }
}))

let app
// Callbacks the store registered through app.ui.notification.on, by type.
let notificationHandlers

vi.mock('@/stores', () => ({ useApp: () => app }))
// The run selector reads the runs store; its own spec pins it.
vi.mock('@/lib/panes/PaneBrowserMatch/BatchPeakRunSelect.vue', () => ({
  default: { template: '<div class="run-select-stub" />' }
}))

const helpStub = {
  set: vi.fn(),
  docUrl: (path = '') => `/docs/${path}`,
  directive: () => ({}),
  right: () => ({}),
  left: () => ({}),
  top: () => ({}),
  bottom: () => ({})
}

const ME = { id: 7, role_id: 200, is_superuser: false }
const BATCH = { sample_batch_id: 'b-1', sample_batch_name: 'Batch 1' }
const WORKSPACE = { workspace_id: 'ws-1', workspace_name: 'Project', my_role: 'editor' }

// MAS-983's stages as the status read serves them, in order.
const STAGES = [
  ['water_ladders', 'Measure water ladders', 'Recognise source ions'],
  ['known_species', 'Decide known species', 'Recognise source ions'],
  ['search', 'Search untargeted', 'Find compositions'],
  ['isotope_lines', 'Isotope lines', 'Find compositions'],
  ['series', 'Detect series', 'Find compositions'],
  ['co_variation', 'Read co-variation', 'Cross-check across the batch'],
  ['continuity', 'Ratio stability', 'Cross-check across the batch'],
  ['batch_checks', 'Read batch checks', 'Cross-check across the batch'],
  ['blends', 'Probable blends', 'Cross-check across the batch']
].map(([stage, label, phase]) => ({ stage, label, phase, state: 'current', version: 1 }))

// Local times, so the status line's "today 14:02" holds in any time zone.
const local = (h, m) => new Date(2026, 10, 16, h, m).toISOString()

const LAST_PASS = {
  batch_peak_run_id: 'run-9',
  action: 'assign',
  first_stage: 'water_ladders',
  utc_started: local(13, 50),
  utc_completed: local(14, 2),
  started_by: { id: 3, username: 'someone' },
  engine_version: '0.7.0',
  n_samples: 24
}

/** A status read for `state`, with `extra` over it. */
function statusOf(state, extra = {}) {
  return {
    sample_batch_id: 'b-1',
    state,
    engine_version: '0.8.0',
    n_samples: 24,
    n_batch_peaks: state === 'none' ? 0 : 120,
    last_pass: ['current', 'behind'].includes(state) ? LAST_PASS : null,
    stopped_pass: null,
    running: null,
    stages: STAGES,
    stale_stages: [],
    needs_fold: state === 'none',
    fold_reasons: state === 'none' ? ['no_batch_peaks'] : [],
    behind: [],
    ...extra
  }
}

const RUNNING_OTHER = {
  batch_peak_run_id: 'run-10',
  action: 'assign',
  process_id: 'their-proc',
  started_by: { id: 3, username: 'someone' },
  utc_started: local(15, 30),
  stage: 'search',
  label: 'Search untargeted',
  stage_index: 2,
  n_stages: 9,
  progress: 0.2,
  cancel_requested_utc: null
}

const FIXTURES = {
  none: statusOf('none'),
  folded_only: statusOf('folded_only'),
  current: statusOf('current'),
  behind_new: statusOf('behind', {
    behind: [{ reason: 'new_samples', count: 12, sample_item_ids: ['s-1'] }],
    stale_stages: ['water_ladders'],
    stages: STAGES.map((s) => ({ ...s, state: 'stale', stale_reason: 'fold' }))
  }),
  behind_library: statusOf('behind', {
    behind: [
      { reason: 'library_changed', count: 1, detail: { target_collections: ['tc-1'] } },
      { reason: 'new_samples', count: 2 }
    ],
    needs_fold: true,
    fold_reasons: ['library_changed']
  }),
  behind_engine: statusOf('behind', {
    behind: [{ reason: 'engine_changed', count: 1, detail: { pass: '0.7.0', now: '0.8.0' } }],
    needs_fold: true
  }),
  running_mine: statusOf('running', {
    running: { ...RUNNING_OTHER, process_id: 'proc-1', started_by: { id: 7, username: 'me' } }
  }),
  running_other: statusOf('running', { running: RUNNING_OTHER }),
  failed: statusOf('failed', {
    stopped_pass: {
      batch_peak_run_id: 'run-10',
      action: 'assign',
      status: 'failed',
      stopped_at: 'series',
      error: 'boom'
    },
    stale_stages: ['series', 'co_variation']
  }),
  cancelled: statusOf('cancelled', {
    stopped_pass: {
      batch_peak_run_id: 'run-10',
      action: 'assign',
      status: 'cancelled',
      stopped_at: 'co_variation'
    }
  }),
  failed_in_fold: statusOf('failed', {
    stopped_pass: {
      batch_peak_run_id: 'run-10',
      action: 'assign',
      status: 'failed',
      stopped_at: 'fold'
    }
  })
}

const peakRow = (id, tier) => ({ batch_peak_id: id, consensus_tier: tier, line_owner_id: null })

function makeApp({
  workspace = WORKSPACE,
  samples = [{}],
  peaks = [peakRow('bp-1', 'assigned')],
  showStages = false,
  user = ME
} = {}) {
  return reactive({
    auth: { user },
    data: {
      batch: { focused: BATCH, focusedId: BATCH.sample_batch_id },
      workspace: { focused: workspace },
      sample: { list: samples, pending: false },
      batchPeak: { list: peaks, pending: false, error: null },
      batchPeakRun: { list: [{ batch_peak_run_id: 'run-9', current: true }], viewingCurrent: true }
    },
    ui: {
      help: helpStub,
      assignmentStages: { show: showStages },
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

// The tooltip directive records its value, so a test can read the reason a
// disabled button gives.
const tooltip = {
  mounted: (el, binding) => (el.dataset.tooltip = binding.value ?? ''),
  updated: (el, binding) => (el.dataset.tooltip = binding.value ?? '')
}

// The menu stub renders every item, submenus flattened, so a test can find
// and click one; `toggle` is what the chevron calls.
const TieredMenuStub = {
  name: 'TieredMenu',
  props: ['model', 'popup'],
  computed: {
    flat() {
      const out = []
      const walk = (items) =>
        (items ?? []).forEach((item) => {
          out.push(item)
          walk(item.items)
        })
      walk(this.model)
      return out
    }
  },
  methods: { toggle() {} },
  template:
    '<div class="menu-stub"><template v-for="(item, i) in flat" :key="i">' +
    '<hr v-if="item.separator" />' +
    '<button v-else :class="[\'menu-item\', item.class]" :disabled="item.disabled"' +
    ' @click="item.command && item.command({ originalEvent: $event })">{{ item.label }}</button>' +
    '</template></div>'
}

const GLOBAL_STUBS = {
  Button: {
    props: ['label', 'disabled', 'loading', 'outlined', 'severity', 'icon'],
    template:
      '<button :disabled="disabled" :data-outlined="String(!!outlined)" :data-loading="String(!!loading)">{{ label }}</button>'
  },
  TieredMenu: TieredMenuStub,
  Popover: { methods: { show() {} }, template: '<div class="popover-stub"><slot /></div>' }
}

const { default: BatchAssignControl } =
  await import('@/lib/panes/PaneBrowserMatch/BatchAssignControl.vue')
const { useBatchAssign, POLL_MS, LEDGER_WAIT_MS } =
  await import('@/lib/panes/PaneBrowserMatch/stores/batchAssign.js')

let wrapper
let assign

async function mountControl(status, options = {}) {
  app = makeApp(options)
  get.mockResolvedValue(status)
  setActivePinia(createPinia())
  wrapper = mount(BatchAssignControl, {
    global: { stubs: GLOBAL_STUBS, directives: { tooltip, help: {} } }
  })
  assign = useBatchAssign()
  await flushPromises()
  return wrapper
}

const line = () => wrapper.find('[data-testid="assign-status-line"]').text()
const detail = () => wrapper.find('[data-testid="assign-status-detail"]')
const main = () => wrapper.find('.assign-main')
const menuItem = (cls) => wrapper.find(`.menu-item.${cls}`)
const tone = () =>
  wrapper
    .find('[data-testid="assign-status"]')
    .classes()
    .find((c) => c.startsWith('tone-'))

/** Deliver an `assign_batch` packet the way the socket would. */
const notify = (payload) =>
  (notificationHandlers.assign_batch ?? []).forEach((cb) =>
    cb({ type: 'assign_batch', ...payload })
  )

/** A 202 acknowledgement, with the process id on the header the route sets. */
const ack = (processId = 'proc-1') => ({
  headers: { 'process-id': processId },
  data: { data: [{ batch_peak_run_id: 'run-10' }] }
})

const apiError = (status, message) => ({ response: { status, data: { error: message } } })

const assignCalls = () => post.mock.calls.filter(([url]) => url.endsWith('/assign'))

beforeEach(() => {
  vi.useFakeTimers({
    toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date']
  })
  vi.setSystemTime(new Date(2026, 10, 16, 16, 0))
  notificationHandlers = {}
  socketHandlers = {}
  post.mockReset()
  get.mockReset()
  post.mockResolvedValue(ack())
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('BatchAssignControl states', () => {
  it('reads the status of the focused batch on focus', async () => {
    await mountControl(FIXTURES.current)

    expect(get).toHaveBeenCalledWith(
      '/batch-peaks/batch/b-1/assignment-status',
      expect.objectContaining({ use: 'read' })
    )
  })

  it('none: Not assigned, Assign batch filled', async () => {
    // A ledger that already lists rows keeps the bar's own copy.
    await mountControl(FIXTURES.none)

    expect(line()).toBe('Not assigned')
    expect(main().text()).toBe('Assign batch')
    expect(main().attributes('data-outlined')).toBe('false')
    expect(tone()).toBe('tone-neutral')
  })

  it('none on an empty ledger: the ledger carries the button, the bar hides its copy', async () => {
    await mountControl(FIXTURES.none, { peaks: [] })

    expect(line()).toBe('Not assigned')
    expect(assign.ledgerOffersAssign).toBe(true)
    expect(main().exists()).toBe(false)
  })

  it('folded_only: Known compositions only, neutral, Assign batch filled', async () => {
    await mountControl(FIXTURES.folded_only)

    expect(line()).toBe('Known compositions only')
    expect(detail().text()).toMatch(/untargeted search and the batch checks have not run/)
    expect(main().text()).toBe('Assign batch')
    expect(main().attributes('data-outlined')).toBe('false')
    // Neutral, not the accent of behind: every batch ingest builds starts here.
    expect(tone()).toBe('tone-neutral')
  })

  it('running, your pass: the phase, Step N of 4, and Cancel', async () => {
    await mountControl(FIXTURES.running_mine)

    expect(wrapper.find('.pi-spinner').exists()).toBe(true)
    expect(line()).toBe('Assigning the batch · Find compositions')
    expect(detail().text()).toBe('Step 3 of 4')
    expect(main().text()).toBe('Cancel')
  })

  it("running, another user's: who, the step and the start, and no button", async () => {
    await mountControl(FIXTURES.running_other)

    expect(line()).toBe('Another user is assigning this batch')
    expect(detail().text()).toBe('Step 3 of 4 · started today 15:30')
    expect(main().exists()).toBe(false)
  })

  it('current: when, up to date, the sample count, and Re-assign outlined', async () => {
    await mountControl(FIXTURES.current)

    expect(line()).toBe('Assigned today 14:02 · up to date')
    expect(detail().text()).toBe('24 samples')
    expect(main().text()).toBe('Re-assign')
    expect(main().attributes('data-outlined')).toBe('true')
    expect(tone()).toBe('tone-ok')
  })

  it('behind on new samples only: the reason in words, Update assignment filled', async () => {
    await mountControl(FIXTURES.behind_new)

    expect(line()).toBe('12 samples added since the last assignment')
    expect(main().text()).toBe('Update assignment')
    expect(main().attributes('data-outlined')).toBe('false')
    expect(tone()).toBe('tone-accent')
  })

  it('behind on anything else: the first reason, the rest counted, Re-assign filled', async () => {
    await mountControl(FIXTURES.behind_library)

    expect(line()).toBe('Target collections or reference lists changed since the last assignment')
    expect(detail().text()).toBe('and 1 other change')
    expect(main().text()).toBe('Re-assign')
    expect(main().attributes('data-outlined')).toBe('false')
  })

  it('behind on the engine: names the version the pass ran under', async () => {
    await mountControl(FIXTURES.behind_engine)

    expect(line()).toBe('Assigned by an older engine (0.7.0)')
  })

  it.each([
    ['failed', 'Stopped at Detect series'],
    ['cancelled', 'Stopped at Read co-variation']
  ])('%s: Stopped at the stage, the stages before it applied, Retry filled', async (key, text) => {
    await mountControl(FIXTURES[key])

    expect(line()).toBe(text)
    expect(detail().text()).toBe('The stages before it were applied.')
    expect(main().text()).toBe('Retry')
    expect(main().attributes('data-outlined')).toBe('false')
    expect(tone()).toBe('tone-warn')
  })

  it('viewer: the status as for anyone, the button disabled with the action that enables it', async () => {
    await mountControl(FIXTURES.behind_new, {
      workspace: { ...WORKSPACE, my_role: 'guest' },
      user: { ...ME, role_id: 100 }
    })

    expect(line()).toBe('12 samples added since the last assignment')
    expect(main().attributes('disabled')).toBeDefined()
    expect(wrapper.find('.assign-main-wrap').attributes('data-tooltip')).toBe(
      'Ask an editor of this workspace to assign it'
    )

    await main().trigger('click')
    expect(assignCalls()).toHaveLength(0)
  })
})

describe('BatchAssignControl pass', () => {
  it('Assign batch posts once and follows its own pass, not another process', async () => {
    await mountControl(FIXTURES.folded_only)
    get.mockResolvedValue(FIXTURES.running_mine)

    await main().trigger('click')
    await flushPromises()
    // A second press while the first is out posts nothing more: the button
    // is Cancel now, and the store refuses a second launch itself.
    expect(main().text()).toBe('Cancel')
    await assign.assign()
    await flushPromises()

    expect(assignCalls()).toHaveLength(1)
    expect(assignCalls()[0][1]).toEqual({ from_stage: 'water_ladders' })
    expect(app.ui.notification.claim).toHaveBeenCalledWith('proc-1')
    expect(assign.pendingProcessId).toBe('proc-1')

    // Its packets name the phase.
    notify({
      status: 'pending',
      process_id: 'proc-1',
      data: {
        sample_batch_id: 'b-1',
        stage: 'co_variation',
        phase: 'Cross-check across the batch',
        stage_index: 5,
        n_stages: 9
      }
    })
    await flushPromises()
    expect(line()).toBe('Assigning the batch · Cross-check across the batch')
    expect(detail().text()).toBe('Step 4 of 4')

    // Another process's end is another pass's: it ends nothing here.
    notify({ status: 'success', process_id: 'their-proc', data: { sample_batch_id: 'b-1' } })
    await flushPromises()
    expect(assign.pendingProcessId).toBe('proc-1')
    expect(main().text()).toBe('Cancel')

    // Its own end does.
    get.mockResolvedValue(FIXTURES.current)
    notify({ status: 'success', process_id: 'proc-1', data: { sample_batch_id: 'b-1' } })
    await flushPromises()
    vi.advanceTimersByTime(LEDGER_WAIT_MS)
    await flushPromises()
    expect(assign.pendingProcessId).toBeNull()
    expect(main().text()).toBe('Re-assign')
  })

  it('is your pass from the launch on, before its first stage names a process', async () => {
    await mountControl(FIXTURES.folded_only)
    // The route opened the run; the pass has not announced a stage yet.
    get.mockResolvedValue(
      statusOf('running', {
        running: {
          batch_peak_run_id: 'run-10',
          action: 'assign',
          process_id: null,
          stage: null,
          started_by: { id: 7 }
        }
      })
    )

    await main().trigger('click')
    await flushPromises()

    expect(line()).toBe('Assigning the batch')
    expect(main().text()).toBe('Cancel')
  })

  it('a second press posts nothing while the status still lags the launch', async () => {
    await mountControl(FIXTURES.folded_only)
    // The read after the launch still says what it said before.

    await assign.assign()
    await flushPromises()
    await assign.assign()
    await flushPromises()

    expect(assignCalls()).toHaveLength(1)
  })

  it('an up-to-date batch re-assigns from the first stage; a behind one from where it is stale', async () => {
    await mountControl(FIXTURES.current)
    await main().trigger('click')
    await flushPromises()
    expect(assignCalls()[0][1]).toEqual({ from_stage: 'water_ladders' })

    wrapper.unmount()
    post.mockClear()
    await mountControl(FIXTURES.behind_new)
    await main().trigger('click')
    await flushPromises()
    // Stale stages: the server starts at the first of them.
    expect(assignCalls()[0][1]).toEqual({})
  })

  it("another user's pass hides the button, and its packets refresh the status", async () => {
    await mountControl(FIXTURES.running_other)
    expect(main().exists()).toBe(false)

    get.mockClear()
    notify({
      status: 'pending',
      process_id: 'their-proc',
      data: { sample_batch_id: 'b-1', stage: 'series', stage_index: 4, n_stages: 9 }
    })
    await flushPromises()
    expect(get).toHaveBeenCalledTimes(1)
    expect(assign.pendingProcessId).toBeNull()

    // Once per stage, not once per packet.
    notify({
      status: 'pending',
      process_id: 'their-proc',
      data: { sample_batch_id: 'b-1', stage: 'series', stage_index: 4, n_stages: 9 }
    })
    await flushPromises()
    expect(get).toHaveBeenCalledTimes(1)
  })

  it('a cancel that finds the pass already ended reports nothing, and reads the status', async () => {
    await mountControl(FIXTURES.running_mine)
    post.mockRejectedValue(apiError(404, 'No pass of the batch stages is running on this batch'))
    get.mockResolvedValue(FIXTURES.current)

    await main().trigger('click')
    await flushPromises()

    expect(assign.launchError).toBeNull()
    expect(main().text()).toBe('Re-assign')
  })

  it('Cancel posts the cancel, and then waits for the pass to stop', async () => {
    await mountControl(FIXTURES.running_mine)

    await main().trigger('click')
    await flushPromises()

    expect(post).toHaveBeenCalledWith(
      '/batch-peaks/batch/b-1/assign/cancel',
      {},
      expect.objectContaining({ errors: 'inline' })
    )
    expect(main().text()).toBe('Stopping…')
    expect(main().attributes('disabled')).toBeDefined()
  })

  it('Retry starts the pass at the stopped stage', async () => {
    await mountControl(FIXTURES.failed)

    await main().trigger('click')
    await flushPromises()

    expect(assignCalls()[0][1]).toEqual({ from_stage: 'series' })
  })

  it('Retry of a pass stopped in the fold folds again', async () => {
    await mountControl(FIXTURES.failed_in_fold)

    await main().trigger('click')
    await flushPromises()

    expect(assignCalls()[0][1]).toEqual({ fold: 'always' })
  })

  it('a refused launch is reported, and the status read again', async () => {
    await mountControl(FIXTURES.folded_only)
    post.mockRejectedValue(apiError(409, 'Another pass is running.'))
    get.mockClear()

    await main().trigger('click')
    await flushPromises()

    expect(assign.launchError).toBe('Another pass is running.')
    expect(assign.launchRefused).toBe(true)
    expect(assign.pendingProcessId).toBeNull()
    expect(get).toHaveBeenCalled()
  })

  it('reads the status again on every peak_assignment_reload of the batch', async () => {
    await mountControl(FIXTURES.folded_only)
    get.mockClear()

    socketHandlers.peak_assignment_reload.forEach((cb) => cb({ sample_batch_id: 'b-1' }))
    socketHandlers.peak_assignment_reload.forEach((cb) => cb({ sample_batch_id: 'b-other' }))
    await flushPromises()

    expect(get).toHaveBeenCalledTimes(1)
  })

  it('reads the status again every half minute while a pass runs, and stops once it ends', async () => {
    await mountControl(FIXTURES.running_other)
    get.mockClear()

    vi.advanceTimersByTime(POLL_MS)
    await flushPromises()
    expect(get).toHaveBeenCalledTimes(1)

    get.mockResolvedValue(FIXTURES.current)
    vi.advanceTimersByTime(POLL_MS)
    await flushPromises()
    expect(get).toHaveBeenCalledTimes(2)

    vi.advanceTimersByTime(POLL_MS * 3)
    await flushPromises()
    expect(get).toHaveBeenCalledTimes(2)
  })
})

describe('BatchAssignControl menu', () => {
  it('Re-assign from scratch folds every sample first', async () => {
    await mountControl(FIXTURES.current)

    await menuItem('from-scratch').trigger('click')
    await flushPromises()

    expect(assignCalls()[0][1]).toEqual({ fold: 'always' })
  })

  it('Export ledger runs the export task', async () => {
    await mountControl(FIXTURES.current)

    await menuItem('export-ledger').trigger('click')
    await flushPromises()

    expect(post).toHaveBeenCalledWith(
      '/batch-peaks/batch/b-1/export',
      {},
      expect.objectContaining({ type: 'export_batch_ledger' })
    )
    expect(assign.exporting).toBe(true)
  })

  it('Run history opens the run selector', async () => {
    await mountControl(FIXTURES.current)

    expect(menuItem('run-history').exists()).toBe(true)
    expect(wrapper.find('.popover-stub .run-select-stub').exists()).toBe(true)
  })

  it('hides Advanced while Show assignment stages is off', async () => {
    await mountControl(FIXTURES.current)

    expect(menuItem('advanced-heading').exists()).toBe(false)
    expect(menuItem('run-from-stage').exists()).toBe(false)
    expect(wrapper.findAll('.menu-item.stage-item')).toHaveLength(0)
  })

  it('lists the stages under their phases with the setting on, each starting the pass there', async () => {
    await mountControl(FIXTURES.current, { showStages: true })

    expect(menuItem('run-from-stage').exists()).toBe(true)
    const headings = wrapper.findAll('.menu-item.phase-heading').map((item) => item.text())
    expect(headings).toEqual([
      'Recognise source ions',
      'Find compositions',
      'Cross-check across the batch'
    ])
    const stages = wrapper.findAll('.menu-item.stage-item')
    expect(stages.map((item) => item.text())).toEqual(STAGES.map((s) => s.label))

    await stages[4].trigger('click')
    await flushPromises()
    expect(assignCalls()[0][1]).toEqual({ from_stage: 'series' })
  })

  it('offers nothing that starts a pass while one runs', async () => {
    await mountControl(FIXTURES.running_other, { showStages: true })

    expect(menuItem('from-scratch').attributes('disabled')).toBeDefined()
    expect(
      wrapper.findAll('.menu-item.stage-item').every((i) => i.attributes('disabled') != null)
    ).toBe(true)
  })
})

describe('BatchAssignControl feedback', () => {
  async function runPassTo(end, endStatus, options = {}) {
    await mountControl(FIXTURES.folded_only, options)
    get.mockResolvedValue(FIXTURES.running_mine)
    await main().trigger('click')
    await flushPromises()
    get.mockResolvedValue(endStatus)
    return end
  }

  it('says nothing more while the control is on screen and the pass ran to its end', async () => {
    await runPassTo(null, FIXTURES.current)
    // The pass assigned one more batch peak.
    app.data.batchPeak.list = [peakRow('bp-1', 'assigned'), peakRow('bp-2', 'assigned')]

    notify({ status: 'success', process_id: 'proc-1', data: { sample_batch_id: 'b-1' } })
    await flushPromises()
    vi.advanceTimersByTime(LEDGER_WAIT_MS)
    await flushPromises()

    expect(app.ui.notification.push).not.toHaveBeenCalled()
    // The change shows in place, on the status line.
    expect(detail().text()).toBe('24 samples · +1 assigned')
  })

  it('off screen, one toast titled Batch assignment gives the change in counts', async () => {
    await runPassTo(null, FIXTURES.current)
    wrapper.unmount()
    wrapper = null

    notify({ status: 'success', process_id: 'proc-1', data: { sample_batch_id: 'b-1' } })
    await flushPromises()
    app.data.batchPeak.list = [
      peakRow('bp-1', 'assigned'),
      peakRow('bp-2', 'assigned'),
      peakRow('bp-3', 'assigned')
    ]
    await flushPromises()

    expect(app.ui.notification.push).toHaveBeenCalledTimes(1)
    expect(app.ui.notification.push).toHaveBeenCalledWith({
      type: 'batch_assignment',
      status: 'success',
      message: 'Assigned the batch: 24 samples, +2 assigned.'
    })
  })

  it('a stopped pass is a warning naming the stage, on screen too', async () => {
    await runPassTo(null, FIXTURES.failed)

    notify({ status: 'error', process_id: 'proc-1', data: { sample_batch_id: 'b-1' } })
    await flushPromises()

    expect(app.ui.notification.push).toHaveBeenCalledTimes(1)
    expect(app.ui.notification.push).toHaveBeenCalledWith({
      type: 'batch_assignment',
      status: 'warning',
      message: 'Stopped at Detect series: the stages before it were applied.'
    })
  })

  it('a pass the person on screen asked to stop needs no toast', async () => {
    await runPassTo(null, FIXTURES.cancelled)
    get.mockResolvedValue(FIXTURES.running_mine)
    await main().trigger('click') // Cancel
    await flushPromises()
    get.mockResolvedValue(FIXTURES.cancelled)

    notify({ status: 'warning', process_id: 'proc-1', data: { sample_batch_id: 'b-1' } })
    await flushPromises()

    expect(app.ui.notification.push).not.toHaveBeenCalled()
    expect(line()).toBe('Stopped at Read co-variation')
  })
})
