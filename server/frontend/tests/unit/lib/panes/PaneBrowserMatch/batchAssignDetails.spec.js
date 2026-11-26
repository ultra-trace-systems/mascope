import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'

// Assign batch's Details: the pass the batch stands on as a checklist of its
// stages, from the pass's `summary.stages` (batch_stages.PassRecord) and the
// status read, with a result line per finished stage, the tier change and
// the settings; and Run history, which names each run and puts an earlier
// one on screen.

const get = vi.fn()
vi.mock('@/api', () => ({ api: { http: { get: (...args) => get(...args) } } }))

let app
vi.mock('@/stores', () => ({ useApp: () => app }))
vi.mock('@/lib/base', () => ({
  BaseRunProvenance: { props: ['run', 'compact'], template: '<span class="provenance" />' }
}))
// The runs store's helpers are pure; the store itself is stood in for below.
vi.mock('@/lib/store', () => ({ useData: () => ({}) }))

let assignStub
vi.mock('@/lib/panes/PaneBrowserMatch/stores/batchAssign.js', async (importOriginal) => ({
  ...(await importOriginal()),
  useBatchAssign: () => assignStub
}))

const { default: BatchAssignDetails } =
  await import('@/lib/panes/PaneBrowserMatch/BatchAssignDetails.vue')
const { skippedWords, stageResult, staleWords, tierChanges, tierCounts, settingsLines } =
  await import('@/lib/panes/PaneBrowserMatch/batchPass.js')

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

const local = (d, h, m) => new Date(2026, 10, d, h, m).toISOString()

// Each stage's outcome as its step reports it: the counts the result line reads.
const OUTCOMES = {
  fold: [
    {
      status: 'success',
      message: 'Folded 24 samples.',
      data: { samples_folded: 24, samples_failed: 0 }
    }
  ],
  water_ladders: [
    {
      status: 'success',
      message: 'Read 6 water-ladder rungs: ...',
      data: { rungs_tested: 6, rungs_passed: 4, claimed: 9 }
    }
  ],
  known_species: [
    { status: 'success', message: 'Decided ...', data: { species: 5, present: 3, absent: 2 } }
  ],
  search: [
    {
      status: 'success',
      message: 'Searched ...',
      data: { anchors_searched: 80, anchors_annotated: 52 }
    }
  ],
  isotope_lines: [
    { status: 'success', message: 'Read ...', data: { merged_rows: 40, stripped: 38 } },
    { status: 'success', message: 'Predicted ...', data: { predicted: 30, lines_stamped: 14 } }
  ],
  series: [
    { status: 'success', message: '...', data: { families_committed: 3, anchors_in_series: 17 } }
  ],
  co_variation: [{ status: 'success', message: '...', data: { co_varying: 4, not_co_varying: 1 } }],
  continuity: [
    { status: 'success', message: '...', data: { pairs_graded: 12, grades: { red: 2 } } }
  ],
  // A step that read nothing says so; its message's head stands in.
  batch_checks: [
    { status: 'success', message: 'Nothing was checked: no batch peak ...', data: {} }
  ],
  blends: [{ status: 'success', message: '...', data: { anchors: 120 } }]
}

/** A pass's summary: `statuses` by stage, every other stage `not_run`. */
function summaryOf(statuses, { first = 'water_ladders', errors = {} } = {}) {
  const entries = [{ stage: 'fold', label: 'Fold the samples', phase: 'Gather samples' }, ...STAGES]
  return {
    first_stage: first,
    stages: entries.map(({ stage, label, phase }) => {
      const status = statuses[stage] ?? 'not_run'
      return {
        stage,
        label,
        phase,
        status,
        ...(status === 'done' ? { outcomes: OUTCOMES[stage] } : {}),
        ...(errors[stage] ? { error: errors[stage] } : {})
      }
    })
  }
}

const allDone = Object.fromEntries(STAGES.map((s) => [s.stage, 'done']))

const SETTINGS = {
  profile: 'auto',
  context: 'auto',
  mz_precision_ppm: null,
  formula_ranges: 'C1-30 H0-60 N0-4 O0-10',
  max_untargeted_peaks: 500,
  peak_intensity_threshold: 0,
  max_alternatives: 3,
  assigned_threshold: 0.8,
  candidate_threshold: 0.5
}

const run = (id, over = {}) => ({
  batch_peak_run_id: id,
  action: 'assign',
  status: 'completed',
  current: false,
  engine: 'mascope',
  batch_peak_run_utc_created: local(16, 13, 50),
  ...over
})

function statusOf(state, extra = {}) {
  return {
    state,
    stages: STAGES,
    stale_stages: [],
    last_pass: null,
    stopped_pass: null,
    running: null,
    ...extra
  }
}

const peak = (id, tier, over = {}) => ({
  batch_peak_id: id,
  consensus_tier: tier,
  line_owner_id: null,
  ...over
})

function makeApp({ runs = [], peaks = [], viewingCurrent = true, progress = [] } = {}) {
  return reactive({
    data: {
      batch: { focusedId: 'b-1' },
      batchPeak: { list: peaks },
      batchPeakRun: {
        list: runs,
        viewing: runs.find((r) => r.current) ?? null,
        viewingCurrent,
        ordinal: (r) => {
          const i = runs.findIndex((x) => x.batch_peak_run_id === r?.batch_peak_run_id)
          return i === -1 ? null : runs.length - i
        },
        focus: vi.fn(),
        backToCurrent: vi.fn()
      }
    },
    ui: {
      help: { docUrl: (path = '') => `/docs/${path}` },
      notification: { progress }
    }
  })
}

const ButtonStub = {
  props: ['label', 'disabled'],
  template: '<button :disabled="disabled">{{ label }}</button>'
}

let wrapper

async function mountDetails({ status, assign = {}, view = 'details', ...appOptions }) {
  app = makeApp(appOptions)
  assignStub = reactive({
    status,
    progress: null,
    pendingProcessId: null,
    runningIsMine: false,
    ...assign
  })
  wrapper = mount(BatchAssignDetails, {
    props: { view },
    global: { stubs: { Button: ButtonStub }, directives: { help: {} } }
  })
  await flushPromises()
  return wrapper
}

const stageRow = (stage) => wrapper.find(`.stage[data-stage="${stage}"]`)
const stateOf = (stage) => stageRow(stage).attributes('data-state')
const states = () =>
  Object.fromEntries(
    wrapper
      .findAll('.stage')
      .map((li) => [li.attributes('data-stage'), li.attributes('data-state')])
  )

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 16, 16, 0))
  get.mockReset()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
})

describe('BatchAssignDetails checklist', () => {
  it('lists the four phases with their stages, the fold first', async () => {
    const pass = run('run-9', { current: true, summary: summaryOf(allDone) })
    await mountDetails({
      status: statusOf('current', { last_pass: { batch_peak_run_id: 'run-9' } }),
      runs: [pass]
    })

    const phases = wrapper.findAll('.phase-name').map((p) => p.text())
    expect(phases).toEqual([
      'Gather samples',
      'Recognise source ions',
      'Find compositions',
      'Cross-check across the batch'
    ])
    expect(wrapper.findAll('.stage').map((s) => s.attributes('data-stage'))).toEqual([
      'fold',
      ...STAGES.map((s) => s.stage)
    ])
  })

  it('renders each stage state from summary.stages: done, failed with its error, cancelled, stale with its reason', async () => {
    const pass = run('run-10', {
      status: 'failed',
      summary: summaryOf(
        {
          water_ladders: 'done',
          known_species: 'done',
          search: 'done',
          isotope_lines: 'done',
          series: 'failed'
        },
        { errors: { series: 'ValueError: no anchors' } }
      )
    })
    await mountDetails({
      status: statusOf('failed', {
        stopped_pass: { batch_peak_run_id: 'run-10', stopped_at: 'series' },
        // The search was stale again since: a sample folded in.
        stale_stages: ['search'],
        stages: STAGES.map((s) =>
          s.stage === 'search' ? { ...s, state: 'stale', stale_reason: 'fold' } : s
        )
      }),
      runs: [pass, run('run-9', { current: true })]
    })

    expect(stateOf('water_ladders')).toBe('done')
    expect(stateOf('series')).toBe('failed')
    expect(stageRow('series').find('[data-testid="stage-error"]').text()).toBe(
      'ValueError: no anchors'
    )
    expect(stateOf('search')).toBe('stale')
    expect(stageRow('search').find('[data-testid="stage-reason"]').text()).toBe(
      'Samples were folded in since'
    )
    expect(stateOf('co_variation')).toBe('not_run')
    expect(stageRow('series').find('[data-testid="stage-state"]').text()).toBe('Failed')
    expect(stageRow('search').find('[data-testid="stage-state"]').text()).toBe('Out of date')
  })

  it('names the samples a current stage skipped, and says when one reads again', async () => {
    const pass = run('run-9', { current: true, summary: summaryOf(allDone) })
    const skipped = [{ sample_item_id: 'si-2', sample_item_name: 'Blank 2', peaks_read: false }]
    await mountDetails({
      status: statusOf('current', {
        last_pass: { batch_peak_run_id: 'run-9' },
        stages: STAGES.map((s) =>
          s.stage === 'search' || s.stage === 'series' ? { ...s, skipped_samples: skipped } : s
        )
      }),
      runs: [pass]
    })

    expect(stateOf('search')).toBe('done')
    expect(stageRow('search').find('[data-testid="stage-skipped"]').text()).toBe(
      'Skipped 1 sample it could not read: Blank 2'
    )
    expect(stageRow('series').find('[data-testid="stage-skipped"]').exists()).toBe(true)
    expect(stageRow('water_ladders').find('[data-testid="stage-skipped"]').exists()).toBe(false)

    wrapper.unmount()
    await mountDetails({
      status: statusOf('behind', {
        last_pass: { batch_peak_run_id: 'run-9' },
        stale_stages: ['search'],
        stages: STAGES.map((s) =>
          s.stage === 'search'
            ? { ...s, state: 'stale', stale_reason: 'read_again', skipped_samples: skipped }
            : s
        )
      }),
      runs: [pass]
    })
    expect(stateOf('search')).toBe('stale')
    expect(stageRow('search').find('[data-testid="stage-reason"]').text()).toBe(
      'A sample it skipped reads again'
    )
  })

  it('marks a cancelled pass’s unfinished stages cancelled, an interrupted one with its error', async () => {
    const pass = run('run-10', {
      status: 'cancelled',
      summary: summaryOf(
        {
          water_ladders: 'done',
          known_species: 'done',
          search: 'cancelled',
          isotope_lines: 'cancelled',
          series: 'cancelled'
        },
        // assign_batch stops an interrupted pass's stage with its error.
        { errors: { search: 'Interrupted: the server restarted' } }
      )
    })
    await mountDetails({
      status: statusOf('cancelled', { stopped_pass: { batch_peak_run_id: 'run-10' } }),
      runs: [pass]
    })

    expect(stateOf('known_species')).toBe('done')
    expect(stateOf('search')).toBe('cancelled')
    expect(stageRow('search').find('[data-testid="stage-state"]').text()).toBe('Cancelled')
    expect(stageRow('search').find('[data-testid="stage-error"]').text()).toBe(
      'Interrupted: the server restarted'
    )
    // A stage cancelled with no error, as a person's cancel leaves it, says nothing more.
    expect(stageRow('isotope_lines').find('[data-testid="stage-error"]').exists()).toBe(false)
  })

  it('lists the stages before a pass started later as not run', async () => {
    const statuses = {
      series: 'done',
      co_variation: 'done',
      continuity: 'done',
      batch_checks: 'done',
      blends: 'done'
    }
    const pass = run('run-9', { current: true, summary: summaryOf(statuses, { first: 'series' }) })
    await mountDetails({
      status: statusOf('current', {
        last_pass: { batch_peak_run_id: 'run-9' },
        // A stage before the one the pass started at, stale since: it says so.
        stale_stages: ['known_species'],
        stages: STAGES.map((s) =>
          s.stage === 'known_species' ? { ...s, state: 'stale', stale_reason: 'curation' } : s
        )
      }),
      runs: [pass]
    })

    expect(states()).toEqual({
      fold: 'not_run',
      water_ladders: 'not_run',
      known_species: 'stale',
      search: 'not_run',
      isotope_lines: 'not_run',
      series: 'done',
      co_variation: 'done',
      continuity: 'done',
      batch_checks: 'done',
      blends: 'done'
    })
    expect(stageRow('search').find('[data-testid="stage-state"]').text()).toBe('Not run')
    expect(stageRow('known_species').find('[data-testid="stage-reason"]').text()).toBe(
      'A batch peak was pinned or released since'
    )
    // A pass that needed no fold says so.
    expect(stageRow('fold').find('[data-testid="stage-state"]').text()).toBe('No rebuild needed')
    expect(wrapper.find('[data-testid="details-heading"]').text()).toContain(
      'Assignment from a stage (Detect series) · current'
    )
  })

  it('gives each finished stage its one-line result', async () => {
    const summary = summaryOf({ fold: 'done', ...allDone }, { first: 'fold' })
    summary.fold = { ran: true, reasons: ['library_changed', 'asked'] }
    const pass = run('run-9', { current: true, summary })
    await mountDetails({
      status: statusOf('current', { last_pass: { batch_peak_run_id: 'run-9' } }),
      runs: [pass]
    })

    const result = (stage) => stageRow(stage).find('[data-testid="stage-result"]').text()
    expect(result('fold')).toBe('24 samples folded')
    expect(stageRow('fold').find('[data-testid="stage-reason"]').text()).toBe(
      'Folded because a target collection or reference list changed; asked for: Re-assign from scratch'
    )
    expect(result('water_ladders')).toBe('4 of 6 rungs passed, 9 batch peaks claimed')
    expect(result('known_species')).toBe('3 of 5 known species present')
    expect(result('search')).toBe('52 of 80 batch peaks assigned a composition')
    expect(result('isotope_lines')).toBe(
      '38 rows folded under their compound, 14 isotope lines stamped'
    )
    expect(result('series')).toBe('3 series families, 17 batch peaks in a series')
    expect(result('co_variation')).toBe('4 pairs of channels co-vary, 1 do not')
    expect(result('continuity')).toBe('12 pairs graded, 2 unstable')
    expect(result('batch_checks')).toBe('Nothing was checked')
    expect(result('blends')).toBe('120 batch peaks read again')
  })
})

describe('BatchAssignDetails while a pass runs', () => {
  const runningPass = (over = {}) => ({
    batch_peak_run_id: 'run-10',
    action: 'assign',
    process_id: 'proc-1',
    started_by: { id: 7, username: 'me' },
    utc_started: local(16, 15, 30),
    stage: 'search',
    stage_index: 2,
    n_stages: 9,
    ...over
  })

  it('shows the stages before the running one done, the running one, and the rest waiting', async () => {
    await mountDetails({
      status: statusOf('running', { running: runningPass() }),
      runs: [
        run('run-10', { status: 'running', summary: { progress: {} } }),
        run('run-9', { current: true })
      ]
    })

    expect(states()).toEqual({
      fold: 'not_run',
      water_ladders: 'done',
      known_species: 'done',
      search: 'running',
      isotope_lines: 'waiting',
      series: 'waiting',
      co_variation: 'waiting',
      continuity: 'waiting',
      batch_checks: 'waiting',
      blends: 'waiting'
    })
  })

  it('follows its packets ahead of the status read, the running stage with its percent', async () => {
    await mountDetails({
      status: statusOf('running', { running: runningPass() }),
      runs: [run('run-10', { status: 'running' }), run('run-9', { current: true })],
      assign: {
        runningIsMine: true,
        pendingProcessId: 'proc-1',
        progress: { stage: 'search', stage_index: 2, n_stages: 9 }
      },
      progress: [
        // Another process's progress is not the stage's.
        { process_id: 'export-1', progress: 80 },
        { process_id: 'proc-1', progress: 0 },
        { process_id: 'child-1', parent_id: 'proc-1', progress: 42.4 }
      ]
    })
    expect(stageRow('search').find('[data-testid="stage-state"]').text()).toBe('Running · 42%')

    // The next packet names the next stage: the checklist moves with it.
    assignStub.progress = { stage: 'isotope_lines', stage_index: 3, n_stages: 9 }
    app.ui.notification.progress = [
      { process_id: 'proc-1', progress: 0 },
      // The next stage's packet, before it has moved: no percent yet, not 0 %.
      { process_id: 'child-2', parent_id: 'proc-1', progress: 0 }
    ]
    await flushPromises()
    expect(stateOf('search')).toBe('done')
    expect(stateOf('isotope_lines')).toBe('running')
    expect(stageRow('isotope_lines').find('[data-testid="stage-state"]').text()).toBe('Running')
    expect(stateOf('series')).toBe('waiting')
  })

  it('counts the fold among a pass that folds first', async () => {
    await mountDetails({
      status: statusOf('running', {
        running: runningPass({ stage: 'fold', stage_index: 0, n_stages: 10 })
      }),
      runs: [run('run-10', { status: 'running' }), run('run-9', { current: true })]
    })

    expect(stateOf('fold')).toBe('running')
    expect(stateOf('water_ladders')).toBe('waiting')
  })

  it('shows a pass started later with the stages before it not run', async () => {
    await mountDetails({
      status: statusOf('running', {
        running: runningPass({ stage: 'continuity', stage_index: 2, n_stages: 5 })
      }),
      runs: [run('run-10', { status: 'running' }), run('run-9', { current: true })]
    })

    expect(stateOf('isotope_lines')).toBe('not_run')
    expect(stateOf('series')).toBe('done')
    expect(stateOf('co_variation')).toBe('done')
    expect(stateOf('continuity')).toBe('running')
    expect(stateOf('blends')).toBe('waiting')
  })

  it('tells of a running pass a step’s own route started', async () => {
    await mountDetails({
      status: statusOf('running', {
        running: runningPass({
          action: 'search_untargeted',
          stage: 'search',
          stage_index: 0,
          n_stages: 7
        })
      }),
      runs: [run('run-9', { current: true, summary: summaryOf(allDone) })]
    })

    expect(stateOf('known_species')).toBe('not_run')
    expect(stateOf('search')).toBe('running')
    expect(stateOf('isotope_lines')).toBe('waiting')
    expect(wrapper.find('[data-testid="details-heading"]').text()).toContain('running…')
  })

  it('stands in for a running pass the runs listing has not read yet', async () => {
    await mountDetails({
      status: statusOf('running', { running: runningPass() }),
      runs: [run('run-9', { current: true })]
    })

    expect(stateOf('search')).toBe('running')
    expect(wrapper.find('[data-testid="details-heading"]').text()).toContain(
      'Assignment · running…'
    )
    expect(wrapper.find('[data-testid="details-heading"]').text()).toContain('by me')
  })

  it('tells of the last pass while a rebuild runs on its own', async () => {
    const pass = run('run-9', { current: true, summary: summaryOf(allDone) })
    get.mockResolvedValue([])
    await mountDetails({
      status: statusOf('running', {
        running: runningPass({ batch_peak_run_id: 'run-11', action: 'rebuild', stage: null }),
        last_pass: { batch_peak_run_id: 'run-9', started_by: { id: 3, username: 'someone' } }
      }),
      runs: [run('run-11', { action: 'rebuild', status: 'running' }), pass, run('run-7')]
    })

    expect(stateOf('search')).toBe('done')
    expect(wrapper.find('[data-testid="details-heading"]').text()).toContain('#2')
    expect(wrapper.find('[data-testid="details-heading"]').text()).toContain('by someone')
  })

  it('draws no tier change while it runs', async () => {
    await mountDetails({
      status: statusOf('running', { running: runningPass() }),
      runs: [run('run-10', { status: 'running' }), run('run-9', { current: true })]
    })

    expect(wrapper.find('[data-testid="tier-changes"]').exists()).toBe(false)
    expect(get).not.toHaveBeenCalled()
  })

  it('draws the tier change once the pass it follows ends', async () => {
    get.mockResolvedValue([peak('a', 'candidate')])
    await mountDetails({
      status: statusOf('running', { running: runningPass() }),
      runs: [run('run-10', { status: 'running' }), run('run-9', { current: true })],
      peaks: [peak('a', 'assigned')]
    })
    expect(get).not.toHaveBeenCalled()

    // The pass completes and becomes current; the run it started from is the same.
    app.data.batchPeakRun.list = [
      run('run-10', { current: true, summary: summaryOf(allDone) }),
      run('run-9')
    ]
    assignStub.status = statusOf('current', { last_pass: { batch_peak_run_id: 'run-10' } })
    await flushPromises()
    expect(get).toHaveBeenCalledWith(
      '/batch-peaks/batch/b-1',
      expect.objectContaining({ params: { min_n_present: 1, batch_peak_run_id: 'run-9' } })
    )
    expect(wrapper.findAll('.tier-change').map((c) => c.text())).toEqual([
      '+1 assigned',
      '−1 candidate'
    ])
  })
})

describe('BatchAssignDetails tier change and settings', () => {
  it('compares the ledger the pass found with the one it left, as chips', async () => {
    const pass = run('run-9', {
      current: true,
      summary: summaryOf(allDone),
      config: { settings: SETTINGS, settings_source: 'batch' }
    })
    get.mockResolvedValue([
      peak('a', 'assigned'),
      peak('b', 'candidate'),
      peak('c', 'candidate'),
      peak('d', 'below_assignability')
    ])
    await mountDetails({
      status: statusOf('current', { last_pass: { batch_peak_run_id: 'run-9' } }),
      runs: [pass, run('run-8', { status: 'failed' }), run('run-7')],
      peaks: [
        peak('a', 'assigned'),
        peak('b', 'assigned'),
        peak('c', 'assigned'),
        peak('d', 'below_assignability'),
        // An isotope line is its owner's, and counts nothing.
        peak('e', 'assigned', { line_owner_id: 'a' })
      ]
    })

    // The snapshot of the run current as the pass started, not of the failed one.
    expect(get).toHaveBeenCalledWith(
      '/batch-peaks/batch/b-1',
      expect.objectContaining({ params: { min_n_present: 1, batch_peak_run_id: 'run-7' } })
    )
    expect(get).toHaveBeenCalledTimes(1)
    const chips = wrapper.findAll('.tier-change').map((c) => c.text())
    expect(chips).toEqual(['+2 assigned', '−2 candidate'])
    expect(wrapper.find('[data-testid="pass-settings"]').text()).toContain("The batch's settings")
  })

  it('counts a stopped pass against the live ledger it left, and none once a run came after', async () => {
    const stopped = run('run-10', {
      status: 'cancelled',
      summary: summaryOf({ water_ladders: 'done' })
    })
    get.mockResolvedValue([peak('a', 'candidate')])
    await mountDetails({
      status: statusOf('cancelled', { stopped_pass: { batch_peak_run_id: 'run-10' } }),
      runs: [stopped, run('run-9', { current: true })],
      peaks: [peak('a', 'assigned')]
    })
    expect(get).toHaveBeenCalledWith(
      '/batch-peaks/batch/b-1',
      expect.objectContaining({ params: { min_n_present: 1, batch_peak_run_id: 'run-9' } })
    )
    expect(wrapper.findAll('.tier-change').map((c) => c.text())).toEqual([
      '+1 assigned',
      '−1 candidate'
    ])

    app.data.batchPeakRun.list = [
      run('run-11', { action: 'import' }),
      ...app.data.batchPeakRun.list
    ]
    await flushPromises()
    expect(wrapper.find('[data-testid="tier-changes"]').exists()).toBe(false)
  })

  it('counts the current pass against the live ledger, also with a failed run after it', async () => {
    const pass = run('run-9', { current: true, summary: summaryOf(allDone) })
    get.mockResolvedValue([peak('a', 'candidate')])
    await mountDetails({
      status: statusOf('current', { last_pass: { batch_peak_run_id: 'run-9' } }),
      runs: [run('run-10', { action: 'import', status: 'failed' }), pass, run('run-7')],
      peaks: [peak('a', 'assigned')]
    })

    expect(get).toHaveBeenCalledTimes(1)
    expect(wrapper.findAll('.tier-change').map((c) => c.text())).toEqual([
      '+1 assigned',
      '−1 candidate'
    ])
  })

  it('reads a snapshot again once it was captured anew', async () => {
    const pass = run('run-9', { current: true, summary: summaryOf(allDone) })
    get.mockResolvedValue([peak('a', 'candidate')])
    await mountDetails({
      status: statusOf('current', { last_pass: { batch_peak_run_id: 'run-9' } }),
      runs: [pass, run('run-7', { snapshot_utc: local(16, 13, 49) })],
      peaks: [peak('a', 'assigned')]
    })
    expect(get).toHaveBeenCalledTimes(1)
    expect(wrapper.findAll('.tier-change').map((c) => c.text())).toEqual([
      '+1 assigned',
      '−1 candidate'
    ])

    // A run's start captures the current run again (batch_runs._snapshot):
    // the earlier capture is not the ledger the run now holds.
    get.mockResolvedValue([peak('a', 'assigned')])
    app.data.batchPeakRun.list = [pass, run('run-7', { snapshot_utc: local(16, 15, 10) })]
    await flushPromises()
    expect(get).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="tier-changes"]').text()).toBe('No change in the tier counts')
  })

  it('reads the pass’s own snapshot when a later run replaced it', async () => {
    const pass = run('run-9', { summary: summaryOf(allDone) })
    get.mockImplementation((url, { params }) =>
      Promise.resolve(
        params.batch_peak_run_id === 'run-7' ? [peak('a', 'candidate')] : [peak('a', 'assigned')]
      )
    )
    await mountDetails({
      status: statusOf('behind', { last_pass: { batch_peak_run_id: 'run-9' } }),
      runs: [run('run-10', { action: 'rebuild', current: true }), pass, run('run-7')],
      peaks: [peak('a', 'below_assignability')]
    })

    expect(get).toHaveBeenCalledTimes(2)
    expect(wrapper.findAll('.tier-change').map((c) => c.text())).toEqual([
      '+1 assigned',
      '−1 candidate'
    ])
  })

  it('says so when nothing moved, and draws nothing while an earlier run is on screen', async () => {
    const pass = run('run-9', { current: true, summary: summaryOf(allDone) })
    get.mockResolvedValue([peak('a', 'assigned')])
    await mountDetails({
      status: statusOf('current', { last_pass: { batch_peak_run_id: 'run-9' } }),
      runs: [pass, run('run-7')],
      peaks: [peak('a', 'assigned')]
    })
    expect(wrapper.find('[data-testid="tier-changes"]').text()).toBe('No change in the tier counts')

    app.data.batchPeakRun.viewingCurrent = false
    await flushPromises()
    expect(wrapper.find('[data-testid="tier-changes"]').exists()).toBe(false)
  })

  it('lists the settings the pass ran under and where they came from', async () => {
    const pass = run('run-9', {
      current: true,
      summary: summaryOf(allDone),
      config: { settings: SETTINGS, settings_source: 'request' }
    })
    get.mockResolvedValue([])
    await mountDetails({
      status: statusOf('current', { last_pass: { batch_peak_run_id: 'run-9' } }),
      runs: [pass, run('run-7')]
    })

    const block = wrapper.find('[data-testid="pass-settings"]')
    expect(block.text()).toContain("Given to this run, in place of the batch's")
    const terms = block.findAll('dt').map((d) => d.text())
    const values = block.findAll('dd').map((d) => d.text())
    expect(Object.fromEntries(terms.map((t, i) => [t, values[i]]))).toEqual({
      'Chemistry profile': 'Auto',
      Context: 'Auto',
      'm/z precision': 'Auto',
      'Formula range': 'C1-30 H0-60 N0-4 O0-10',
      'Peak ceiling': '500',
      'Intensity threshold': '0',
      'Alternatives kept': '3',
      'Tier bands': 'assigned ≥ 0.8, candidate ≥ 0.5'
    })
  })
})

describe('BatchAssignDetails Change settings', () => {
  it('asks for Edit assignment settings from beside the settings', async () => {
    const pass = run('run-9', {
      current: true,
      summary: summaryOf(allDone),
      config: { settings: SETTINGS, settings_source: 'batch' }
    })
    get.mockResolvedValue([])
    await mountDetails({
      status: statusOf('current', { last_pass: { batch_peak_run_id: 'run-9' } }),
      runs: [pass, run('run-7')]
    })

    const change = wrapper.find('[data-testid="pass-settings"] [data-testid="change-settings"]')
    expect(change.text()).toBe('Change settings')
    await change.trigger('click')
    expect(wrapper.emitted('change-settings')).toHaveLength(1)
  })
})

describe('BatchAssignDetails Run history', () => {
  const RUNS = [
    run('r6', {
      status: 'running',
      summary: { progress: { stage: 'search' } },
      config: { from_stage: 'water_ladders' }
    }),
    run('r5', { current: true, summary: summaryOf(allDone) }),
    // An earlier completed run has the snapshot the next run's start kept.
    run('r4', {
      summary: summaryOf({ series: 'done' }, { first: 'series' }),
      snapshot_utc: local(16, 14, 0)
    }),
    run('r3', { action: 'detect_series', status: 'cancelled' }),
    run('r2', { action: 'import', engine: 'peaky', snapshot_utc: local(16, 13, 0) }),
    run('r1', { action: 'fold', snapshot_utc: local(16, 12, 0) })
  ]

  it('opens from Details’ link', async () => {
    await mountDetails({
      status: statusOf('current', { last_pass: { batch_peak_run_id: 'r5' } }),
      runs: RUNS.slice(1)
    })
    get.mockResolvedValue([])

    await wrapper.find('[data-testid="to-history"]').trigger('click')
    expect(wrapper.emitted('update:view')).toEqual([['history']])
  })

  it('lists the runs newest first: a pass, a later-start pass, a single stage, an import and a fold', async () => {
    await mountDetails({ status: statusOf('running'), runs: RUNS, view: 'history' })

    const rows = wrapper.findAll('.history-row')
    expect(rows.map((r) => r.find('[data-testid="run-label"]').text())).toEqual([
      'Assignment',
      'Assignment',
      'Assignment from a stage (Detect series)',
      'Detect series',
      'Import',
      'Folded samples'
    ])
    expect(rows.map((r) => r.find('.row-state').text())).toEqual([
      'running…',
      'current',
      'completed',
      'cancelled',
      'completed',
      'completed'
    ])
    expect(rows.map((r) => r.find('.row-ordinal').text())).toEqual([
      '#6',
      '#5',
      '#4',
      '#3',
      '#2',
      '#1'
    ])
    expect(wrapper.findAll('.provenance')).toHaveLength(6)
  })

  it('picking an earlier run puts it on screen; picking the current goes back to it', async () => {
    await mountDetails({ status: statusOf('current'), runs: RUNS.slice(1), view: 'history' })
    const runs = app.data.batchPeakRun

    await wrapper.find('.history-row[data-run="r4"]').trigger('click')
    expect(runs.focus).toHaveBeenCalledWith(expect.objectContaining({ batch_peak_run_id: 'r4' }))

    await wrapper.find('.history-row[data-run="r5"]').trigger('click')
    expect(runs.backToCurrent).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('picked')).toHaveLength(2)
  })

  it('marks the run on screen', async () => {
    await mountDetails({ status: statusOf('current'), runs: RUNS.slice(1), view: 'history' })
    app.data.batchPeakRun.viewing = RUNS[3]
    await flushPromises()

    expect(wrapper.find('.history-row.viewing').attributes('data-run')).toBe('r3')
  })
})

describe('batchPass helpers', () => {
  it('words the stale marks', () => {
    const status = { stages: STAGES }
    expect(staleWords('upstream:series', status)).toBe('Detect series ran again since')
    expect(staleWords('curation', status)).toBe('A batch peak was pinned or released since')
    expect(staleWords(null, status)).toBe('Out of date')
    expect(staleWords('read_again', status)).toBe('A sample it skipped reads again')
  })

  it('names the skipped samples, the first few, and counts the rest', () => {
    expect(skippedWords(null)).toBeNull()
    expect(skippedWords([])).toBeNull()
    const many = Array.from({ length: 7 }, (_, i) => ({
      sample_item_id: `si-${i}`,
      sample_item_name: i === 0 ? null : `S${i}`
    }))
    expect(skippedWords(many)).toBe(
      'Skipped 7 samples it could not read: si-0, S1, S2, S3, S4 and 2 more'
    )
  })

  it('says how many samples the fold skipped', () => {
    expect(
      stageResult({ stage: 'fold', outcomes: [{ data: { samples_folded: 1, samples_failed: 2 } }] })
    ).toBe('1 sample folded, 2 skipped')
  })

  it('takes the message’s head where the counts are not there', () => {
    expect(
      stageResult({
        stage: 'search',
        outcomes: [{ message: 'No untargeted search was run: every ...', data: {} }]
      })
    ).toBe('No untargeted search was run')
    expect(stageResult({ stage: 'search', outcomes: [] })).toBeNull()
  })

  it('counts tiers without isotope lines, and signs the change', () => {
    const before = tierCounts([peak('a', 'candidate')])
    const after = tierCounts([peak('a', 'assigned'), peak('b', 'assigned', { line_owner_id: 'a' })])
    expect(tierChanges(before, after).map((c) => c.label)).toEqual(['+1 assigned', '−1 candidate'])
  })

  it('records no settings for a run that kept none', () => {
    expect(settingsLines(null)).toEqual([])
    expect(settingsLines({ from_stage: 'series' })).toEqual([])
  })
})
