import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'

import { num } from '@/lib/formatters'
import { runName, runNotice } from '@/lib/panes/PanePeakAssign/card/cardReadings.js'

// The inspector card's states (docs/dev/assignment_ledger_and_card.md,
// section 4.2): an earlier run, a decoy, the viewer role, loading, a focused
// peak with no ledger row, and a card opened from the batch scope. The card
// itself is covered by panePeakAssign.spec.js; this file mounts it with a
// small app whose run store, workspace role and batch jump a test sets.

const PEAK = { peak_id: 'p-1', mz: 200.12345, height: 12345 }

const ROW = {
  peak_assignment_id: 'pa-1',
  peak_assignment_run_id: 'run-2',
  sample_item_id: 'si-1',
  sample_peak_id: 'p-1',
  sample_peak_mz: 200.12345,
  sample_peak_intensity: 12345,
  assigned_formula: 'C6H12O6',
  ion_formula: 'C6H11O6-',
  tier: 'assigned',
  role: 'M0',
  fit_score: 0.9
}

// The alternatives' Use and a hand override's Undo both read the detail.
const DETAIL = {
  ...ROW,
  provenance: {
    manual: {
      action: 'promote_alternative',
      previous_formula: 'C7H16O5',
      previous: { assigned_formula: 'C7H16O5', ionization_mechanism_id: 'mech-1' }
    }
  },
  alternatives: [
    { assigned_formula: 'C7H16O5', ion_formula: 'C7H15O5-', ionization_mechanism_id: 'mech-1' }
  ]
}

const RUN_OLD = {
  peak_assignment_run_id: 'run-1',
  sample_item_id: 'si-1',
  status: 'completed',
  peak_assignment_run_utc_completed: '2026-11-01T10:00:00Z'
}
const RUN_NEW = {
  peak_assignment_run_id: 'run-2',
  sample_item_id: 'si-1',
  status: 'completed',
  peak_assignment_run_utc_completed: '2026-11-20T10:00:00Z'
}

const verify = vi.fn(() => Promise.resolve(null))
const release = vi.fn(() => Promise.resolve(null))
const curate = vi.fn(() => Promise.resolve(null))
const stepTo = vi.fn(() => Promise.resolve('peak'))
const loadDetail = vi.fn(() => Promise.resolve())

// What a test sets, read by the app through getters so a change after the
// mount reaches the card's computeds.
const state = reactive({})

function reset() {
  Object.assign(state, {
    focusedPeak: PEAK,
    focusedAssignment: { ...ROW, source: 'manual' },
    detail: DETAIL,
    run: RUN_NEW,
    runs: [RUN_NEW, RUN_OLD],
    latest: RUN_NEW,
    role: 'editor',
    verdict: null,
    opened: null,
    samples: [
      { sample_item_id: 'si-1', sample_item_name: 'Sample one' },
      { sample_item_id: 'si-2', sample_item_name: 'Sample two' },
      { sample_item_id: 'si-3', sample_item_name: 'Sample three' }
    ],
    sampleId: 'si-1',
    measuring: false
  })
}

function makeApp() {
  return {
    auth: { user: { id: 1, is_superuser: false } },
    data: {
      workspace: {
        get focused() {
          return { workspace_id: 'w-1', my_role: state.role }
        }
      },
      sample: {
        get focusedId() {
          return state.sampleId
        },
        get list() {
          return state.samples
        }
      },
      batch: { focusedId: 'sb-1' },
      peak: {
        get list() {
          return [PEAK]
        },
        get focused() {
          return state.focusedPeak
        },
        focus: vi.fn()
      },
      peakAssignment: {
        run: {
          get list() {
            return state.runs
          },
          get latestCompleted() {
            return state.latest
          }
        },
        peak: {
          list: [],
          get run() {
            return state.run
          },
          forPeak: () => state.focusedAssignment,
          detailOf: (id) =>
            id != null && id === state.focusedAssignment?.peak_assignment_id ? state.detail : null,
          familyOf: () => (state.focusedAssignment ? [state.focusedAssignment] : []),
          m0Of: (row) => row,
          loadDetail,
          altScoresOf: () => null,
          altScoresPending: () => false,
          loadAltScores: vi.fn(() => Promise.resolve([])),
          evidenceOf: () => null,
          evidencePending: () => state.measuring,
          loadEvidence: vi.fn(() => Promise.resolve(null)),
          candidateMeasureKey: () => null,
          candidateMeasureOf: () => null,
          candidateMeasurePending: () => false,
          loadCandidateMeasure: vi.fn(() => Promise.resolve(null)),
          curate
        },
        verification: { forAssignment: () => state.verdict, verify },
        anchorContext: { overlayFor: () => null }
      },
      ionization: { mechanism: { list: [] } }
    },
    ui: {
      help: { set: vi.fn(), docUrl: (path = '') => `/docs/${path}` }
    }
  }
}

vi.mock('@/stores', () => ({ useApp: () => makeApp() }))
vi.mock('@/lib/panes/PanePeakAssign/stores/batchPeakCuration.js', () => ({
  useBatchPeakCuration: () => ({ curating: false, curate: vi.fn(), release })
}))
vi.mock('@/lib/panes/PaneBrowserMatch/stores/batchPeakJump.js', () => ({
  useBatchPeakJump: () => ({
    get opened() {
      return state.opened
    },
    stepTo
  })
}))
vi.mock('@/lib/base', () => ({
  BaseClaimStructure: { template: '<div />' },
  BaseTierTag: { props: ['tier'], template: '<span class="tier-tag" />' },
  BaseLevelTag: { props: ['level'], template: '<span />' },
  BaseVerdictBadge: { props: ['record'], template: '<span class="verdict-badge" />' },
  BaseEvidenceBadge: { template: '<span />' }
}))

const PopoverStub = {
  data: () => ({ visible: false }),
  methods: {
    show() {
      this.visible = true
    },
    hide() {
      this.visible = false
    }
  },
  template: '<div v-if="visible" class="popover" role="dialog"><slot /></div>'
}

const STUBS = {
  // Draws `disabled`, so a test reads whether a control is live.
  Button: {
    props: ['label', 'disabled'],
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\', $event)">{{ label }}</button>'
  },
  InputText: { template: '<input />' },
  Popover: PopoverStub,
  RadioButton: { template: '<input type="radio" />' },
  Skeleton: { template: '<span class="skeleton" />' }
}

const { default: PanePeakAssign } = await import('@/lib/panes/PanePeakAssign/PanePeakAssign.vue')

async function mountCard() {
  const record = (el, binding) => el.setAttribute('data-tooltip', binding.value ?? '')
  const wrapper = mount(PanePeakAssign, {
    global: {
      stubs: STUBS,
      directives: { tooltip: { mounted: record, updated: record }, help: {} }
    }
  })
  await flushPromises()
  return wrapper
}

// Every test reads the one `state`, so a card left mounted would answer it too.
enableAutoUnmount(afterEach)

const byTestId = (wrapper, id) => wrapper.find(`[data-testid="${id}"]`)
const buttonsLabelled = (wrapper, label) =>
  wrapper.findAll('button').filter((button) => button.text() === label)

beforeEach(() => {
  reset()
  loadDetail.mockImplementation(() => Promise.resolve())
})
afterEach(() => vi.clearAllMocks())

describe('card states: the run on view', () => {
  it("offers the sample's latest run its controls, with no notice", async () => {
    state.focusedAssignment = { ...ROW, source: 'manual' }
    const wrapper = await mountCard()

    expect(wrapper.find('.run-notice').exists()).toBe(false)
    for (const label of ['Confirm', 'Reject', 'Unsure']) {
      expect(buttonsLabelled(wrapper, label)[0].attributes('disabled')).toBeUndefined()
    }
    expect(byTestId(wrapper, 'manual-undo').exists()).toBe(true)
    expect(buttonsLabelled(wrapper, 'Use')).toHaveLength(1)
  })

  it('names an earlier run, disables the verdict bar with its reason and hides Use and Undo', async () => {
    state.run = RUN_OLD
    const wrapper = await mountCard()

    const notice = byTestId(wrapper, 'run-notice-earlier')
    expect(notice.exists()).toBe(true)
    expect(notice.text()).toContain('Read only: an earlier run, run #1 of 2')
    // Under the answer, before anything else of the card.
    const answer = byTestId(wrapper, 'answer').element
    expect(answer.nextElementSibling).toBe(notice.element)

    for (const label of ['Confirm', 'Reject', 'Unsure']) {
      const [button] = buttonsLabelled(wrapper, label)
      expect(button.attributes('disabled')).toBeDefined()
    }
    expect(byTestId(wrapper, 'verify-read-only').text()).toContain('latest completed run')

    expect(byTestId(wrapper, 'manual-undo').exists()).toBe(false)
    expect(buttonsLabelled(wrapper, 'Use')).toHaveLength(0)
    expect(byTestId(wrapper, 'curate-read-only').exists()).toBe(true)

    // A disabled button sends nothing, and nor does the model's own guard.
    await buttonsLabelled(wrapper, 'Confirm')[0].trigger('click')
    expect(wrapper.find('.popover').exists()).toBe(false)
    wrapper.vm.openVerdict('confirmed', new MouseEvent('click'))
    await flushPromises()
    expect(wrapper.find('.popover').exists()).toBe(false)
    expect(await wrapper.vm.submitVerdict('rejected')).toBe(false)
    await wrapper.vm.promoteAlternative(DETAIL.alternatives[0], 0)
    expect(verify).not.toHaveBeenCalled()
    expect(curate).not.toHaveBeenCalled()
  })

  it('keeps a recorded verdict on an earlier run, with no Change', async () => {
    state.run = RUN_OLD
    state.verdict = { verdict: 'confirmed', evidence_level: 'msms' }
    const wrapper = await mountCard()

    expect(byTestId(wrapper, 'verdict-recorded').exists()).toBe(true)
    expect(byTestId(wrapper, 'verdict-change').exists()).toBe(false)
  })

  it('names the batch ledger as the batch ledger, not as an earlier run', async () => {
    // Listed last, with its last batch-peak write as its completion time.
    const ledger = {
      peak_assignment_run_id: 'fold-si-1',
      sample_item_id: 'si-1',
      engine: 'batch',
      status: 'completed',
      peak_assignment_run_utc_completed: '2026-11-21T10:00:00Z'
    }
    state.run = ledger
    state.runs = [RUN_NEW, RUN_OLD, ledger]
    const wrapper = await mountCard()

    const notice = byTestId(wrapper, 'run-notice-ledger')
    expect(notice.text()).toContain('Read only: the batch ledger, derived from the batch peaks')
    expect(notice.text()).not.toContain('earlier')
    expect(byTestId(wrapper, 'run-notice-earlier').exists()).toBe(false)
    expect(buttonsLabelled(wrapper, 'Confirm')[0].attributes('disabled')).toBeDefined()
    expect(byTestId(wrapper, 'verify-read-only').text()).toContain('not a run of this sample')
  })

  it('names a newer run that has not completed as newer, with its state', async () => {
    const running = {
      peak_assignment_run_id: 'run-3',
      sample_item_id: 'si-1',
      status: 'running',
      peak_assignment_run_utc_created: '2026-11-21T10:00:00Z'
    }
    state.run = running
    state.runs = [running, RUN_NEW, RUN_OLD]
    const wrapper = await mountCard()

    const notice = byTestId(wrapper, 'run-notice-newer')
    expect(notice.text()).toContain('Read only: a newer run still running, run #3 of 3, started')
    expect(byTestId(wrapper, 'run-notice-earlier').exists()).toBe(false)
    expect(buttonsLabelled(wrapper, 'Confirm')[0].attributes('disabled')).toBeDefined()
    expect(byTestId(wrapper, 'verify-read-only').text()).toContain('has not completed')
  })

  it('marks a decoy run with its arm, and records nothing on it', async () => {
    state.run = { ...RUN_NEW, peak_assignment_run_id: 'run-3', decoy: 'mz_shift_ppm=+9' }
    const wrapper = await mountCard()

    const notice = byTestId(wrapper, 'run-notice-decoy')
    expect(notice.text()).toContain('arm mz_shift_ppm=+9')
    expect(byTestId(wrapper, 'run-notice-earlier').exists()).toBe(false)
    expect(buttonsLabelled(wrapper, 'Confirm')[0].attributes('disabled')).toBeDefined()
    expect(buttonsLabelled(wrapper, 'Use')).toHaveLength(0)
  })
})

describe('card states: the viewer role', () => {
  it('offers a viewer no control and sends no request', async () => {
    state.role = 'viewer'
    state.verdict = { verdict: 'confirmed', evidence_level: 'msms' }
    const wrapper = await mountCard()

    // The recorded verdict, and nothing to change it with.
    expect(byTestId(wrapper, 'verdict-recorded').exists()).toBe(true)
    expect(byTestId(wrapper, 'verdict-change').exists()).toBe(false)
    expect(byTestId(wrapper, 'manual-undo').exists()).toBe(false)
    expect(buttonsLabelled(wrapper, 'Use')).toHaveLength(0)
    expect(byTestId(wrapper, 'curate-denied').exists()).toBe(true)

    expect(await wrapper.vm.submitVerdict('rejected')).toBe(false)
    await wrapper.vm.promoteAlternative(DETAIL.alternatives[0], 0)
    expect(verify).not.toHaveBeenCalled()
    expect(curate).not.toHaveBeenCalled()
  })

  it("offers a viewer neither a batch pin's Release nor a derived row's Use", async () => {
    state.role = 'viewer'
    state.run = { ...RUN_NEW, engine: 'batch' }
    state.focusedAssignment = { ...ROW, batch_peak_id: 'bp-1' }
    state.detail = {
      ...ROW,
      batch_peak_id: 'bp-1',
      provenance: {
        batch_peak: { consensus_formula: 'C6H12O6' },
        manual: { action: 'promote_identity', previous: { consensus_formula: 'C7H16O5' } }
      },
      alternatives: [{ assigned_formula: 'C7H16O5', ionization_mechanism_id: 'm-1', candidate: 1 }]
    }
    const wrapper = await mountCard()

    expect(byTestId(wrapper, 'batch-pin-note').exists()).toBe(true)
    expect(byTestId(wrapper, 'batch-release').exists()).toBe(false)
    expect(buttonsLabelled(wrapper, 'Use')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('Derived from the batch ledger')
    await wrapper.vm.releaseOverride()
    expect(release).not.toHaveBeenCalled()

    // An editor on the same row gets both.
    state.role = 'editor'
    await flushPromises()
    expect(byTestId(wrapper, 'batch-release').exists()).toBe(true)
    expect(wrapper.text()).toContain('Derived from the batch ledger')
  })

  it('says why a viewer has no verdict buttons where nothing is recorded', async () => {
    state.role = 'viewer'
    const wrapper = await mountCard()

    for (const label of ['Confirm', 'Reject', 'Unsure']) {
      expect(buttonsLabelled(wrapper, label)).toHaveLength(0)
    }
    expect(byTestId(wrapper, 'verify-denied').text()).toContain('Editor access is required')
    expect(byTestId(wrapper, 'verify-read-only').exists()).toBe(false)
  })

  it('tells a viewer on an earlier run of the role, not of the run', async () => {
    state.role = 'viewer'
    state.run = RUN_OLD
    const wrapper = await mountCard()

    expect(byTestId(wrapper, 'run-notice-earlier').exists()).toBe(true)
    expect(byTestId(wrapper, 'verify-denied').exists()).toBe(true)
    expect(byTestId(wrapper, 'verify-read-only').exists()).toBe(false)
  })
})

describe('card states: loading', () => {
  it('draws skeletons in each section until the detail lands, then the content', async () => {
    state.detail = null
    let finish
    loadDetail.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    const wrapper = await mountCard()

    expect(byTestId(wrapper, 'skeleton-why').exists()).toBe(true)
    expect(byTestId(wrapper, 'skeleton-measurements').exists()).toBe(true)
    expect(wrapper.findAll('[data-testid="skeleton-section"]')).toHaveLength(4)
    expect(byTestId(wrapper, 'measurements').exists()).toBe(false)
    // The answer and the verdict bar do not wait for it.
    expect(byTestId(wrapper, 'answer').exists()).toBe(true)
    expect(byTestId(wrapper, 'verdict-bar').exists()).toBe(true)

    state.detail = DETAIL
    finish(DETAIL)
    await flushPromises()

    expect(wrapper.find('.card-skeleton').exists()).toBe(false)
    expect(byTestId(wrapper, 'measurements').exists()).toBe(true)
  })

  it('draws no skeleton for a row whose detail is already loaded', async () => {
    loadDetail.mockImplementation(() => new Promise(() => {}))
    const wrapper = await mountCard()

    expect(wrapper.find('.card-skeleton').exists()).toBe(false)
    expect(byTestId(wrapper, 'measurements').exists()).toBe(true)
  })

  it('follows the focused row: an earlier row landing late leaves the new one loading', async () => {
    state.detail = null
    const pending = []
    loadDetail.mockImplementation(() => new Promise((resolve) => pending.push(resolve)))
    const wrapper = await mountCard()
    expect(wrapper.find('.card-skeleton').exists()).toBe(true)

    state.focusedAssignment = { ...ROW, peak_assignment_id: 'pa-2', source: 'manual' }
    await flushPromises()
    expect(pending).toHaveLength(2)
    // The first row's detail lands after the focus moved on: still loading.
    pending[0](null)
    await flushPromises()
    expect(wrapper.find('.card-skeleton').exists()).toBe(true)

    pending[1](null)
    await flushPromises()
    expect(wrapper.find('.card-skeleton').exists()).toBe(false)
  })

  it('leaves a row whose detail is cached unloaded, while another row still loads', async () => {
    state.detail = null
    loadDetail.mockImplementation(() => new Promise(() => {}))
    const wrapper = await mountCard()
    expect(wrapper.find('.card-skeleton').exists()).toBe(true)

    // The next row's detail is in the cache: it is drawn at once.
    state.detail = DETAIL
    state.focusedAssignment = { ...ROW, peak_assignment_id: 'pa-2', source: 'manual' }
    await flushPromises()
    expect(wrapper.find('.card-skeleton').exists()).toBe(false)
  })

  it("draws a derived row's measurement tiles as skeletons while it is measured", async () => {
    state.run = { ...RUN_NEW, engine: 'batch' }
    state.measuring = true
    const wrapper = await mountCard()

    expect(byTestId(wrapper, 'measurements-loading').exists()).toBe(true)
    expect(byTestId(wrapper, 'tile-mass').exists()).toBe(false)

    state.measuring = false
    await flushPromises()
    expect(byTestId(wrapper, 'measurements-loading').exists()).toBe(false)
    expect(byTestId(wrapper, 'tile-mass').exists()).toBe(true)
  })

  it('drops the skeletons when the detail fails to load', async () => {
    state.detail = null
    loadDetail.mockImplementation(() => Promise.reject(new Error('down')))
    const wrapper = await mountCard()

    expect(wrapper.find('.card-skeleton').exists()).toBe(false)
    expect(byTestId(wrapper, 'measurements').exists()).toBe(true)
  })
})

describe('card states: a focused peak with no ledger row', () => {
  it('says so with the peak, and offers the formula search', async () => {
    state.focusedAssignment = null
    const wrapper = await mountCard()

    const card = byTestId(wrapper, 'no-row-card')
    expect(card.text()).toContain('No assignment')
    expect(byTestId(wrapper, 'no-row-line').text()).toBe(
      'The run on view has no row for this peak.'
    )
    const summary = byTestId(wrapper, 'peak-summary').text()
    expect(summary).toContain(`m/z ${num.mz.format(PEAK.mz)}`)
    expect(summary).toContain(`intensity ${num.peakIntensity.format(PEAK.height)}`)

    const search = byTestId(wrapper, 'find-other-formulas')
    expect(search.text()).toBe('Find other formulas for this peak')
    await search.trigger('click')
    expect(wrapper.vm.showSearch).toBe(true)
  })

  it('names the run on view on the no-row card too', async () => {
    state.focusedAssignment = null
    state.run = RUN_OLD
    const wrapper = await mountCard()

    const card = byTestId(wrapper, 'no-row-card')
    const notice = byTestId(wrapper, 'run-notice-earlier')
    expect(notice.exists()).toBe(true)
    expect(card.element.contains(notice.element)).toBe(true)
    expect(notice.text()).toContain('Read only: an earlier run, run #1 of 2')
  })

  it('says when no run has assigned the sample', async () => {
    state.focusedAssignment = null
    state.run = null
    const wrapper = await mountCard()

    expect(byTestId(wrapper, 'no-row-line').text()).toBe('No run has assigned this sample yet.')
  })
})

describe('card states: opened from the batch scope', () => {
  const opened = () => ({
    batch_peak_id: 'bp-1',
    sample_batch_id: 'sb-1',
    members: [
      { sample_item_id: 'si-1', sample_peak_id: 'p-1', intensity: 900 },
      { sample_item_id: 'si-2', sample_peak_id: 'q-7', intensity: 300 },
      { sample_item_id: 'si-3', sample_peak_id: 'r-2', intensity: 10 }
    ]
  })

  it('names the brightest sample and steps to the next one', async () => {
    state.opened = opened()
    const wrapper = await mountCard()

    const line = byTestId(wrapper, 'batch-opened')
    expect(line.text()).toContain('In Sample one: the brightest of 3 samples')
    const step = byTestId(wrapper, 'batch-other-samples')
    expect(step.attributes('data-tooltip')).toContain('Sample two')

    await step.trigger('click')
    expect(stepTo).toHaveBeenCalledWith(1)
  })

  it('names a later member by its place, and wraps from the last to the first', async () => {
    state.opened = opened()
    state.sampleId = 'si-3'
    state.focusedPeak = { ...PEAK, peak_id: 'r-2' }
    const wrapper = await mountCard()

    expect(byTestId(wrapper, 'batch-opened').text()).toContain('In Sample three: 3 of 3 samples')
    await byTestId(wrapper, 'batch-other-samples').trigger('click')
    expect(stepTo).toHaveBeenCalledWith(0)
  })

  it('says nothing of the batch on another peak, in another batch, or with one member', async () => {
    state.opened = opened()
    state.focusedPeak = { ...PEAK, peak_id: 'p-other' }
    let wrapper = await mountCard()
    expect(byTestId(wrapper, 'batch-opened').exists()).toBe(false)

    state.focusedPeak = PEAK
    state.opened = { ...opened(), sample_batch_id: 'sb-other' }
    wrapper = await mountCard()
    expect(byTestId(wrapper, 'batch-opened').exists()).toBe(false)

    state.opened = { ...opened(), members: opened().members.slice(0, 1) }
    wrapper = await mountCard()
    expect(byTestId(wrapper, 'batch-opened').text()).toContain('the brightest of 1 samples')
    expect(byTestId(wrapper, 'batch-other-samples').exists()).toBe(false)
  })
})

describe('runNotice', () => {
  it('reads nothing on the latest run, or with no completed run to compare', () => {
    expect(runNotice(RUN_NEW, RUN_NEW, [RUN_NEW, RUN_OLD])).toBeNull()
    expect(runNotice(RUN_OLD, null, [RUN_OLD])).toBeNull()
    expect(runNotice(null, RUN_NEW, [RUN_NEW])).toBeNull()
  })

  it('puts a decoy first, even with nothing completed', () => {
    expect(runNotice({ ...RUN_OLD, decoy: 'swap_adducts' }, null, []).kind).toBe('decoy')
  })

  it('names a run by its place and when it finished', () => {
    expect(runName(RUN_OLD, [RUN_NEW, RUN_OLD])).toMatch(/^run #1 of 2, completed /)
    expect(runName({ peak_assignment_run_id: 'x' }, [])).toBe('run')
  })

  it('says a run that has not finished started, at its creation time', () => {
    const created = '2026-11-21T10:00:00Z'
    const run = { peak_assignment_run_id: 'run-3', peak_assignment_run_utc_created: created }
    expect(runName(run, [run, RUN_NEW])).toBe(
      `run #2 of 2, started ${new Date(created).toLocaleString()}`
    )
  })

  it('names the batch ledger by its last write, wherever it is listed', () => {
    const written = '2026-11-21T10:00:00Z'
    const ledger = {
      peak_assignment_run_id: 'fold-si-1',
      engine: 'batch',
      status: 'completed',
      peak_assignment_run_utc_completed: written
    }
    const notice = runNotice(ledger, RUN_NEW, [RUN_NEW, RUN_OLD, ledger])
    expect(notice.kind).toBe('ledger')
    expect(notice.text).toBe(
      `Read only: the batch ledger, derived from the batch peaks, last written ${new Date(written).toLocaleString()}`
    )
    expect(notice.reason).toContain('latest completed run')
    const undated = { ...ledger, peak_assignment_run_utc_completed: null }
    expect(runNotice(undated, RUN_NEW, [RUN_NEW, undated]).text).toBe(
      'Read only: the batch ledger, derived from the batch peaks'
    )
    // The sample's only completed run is its result, ledger or not.
    expect(runNotice(ledger, ledger, [ledger])).toBeNull()
  })

  it('names a run listed before the latest completed one as newer, by its state', () => {
    const newer = (status) => ({ peak_assignment_run_id: 'run-3', status })
    const read = (status) => runNotice(newer(status), RUN_NEW, [newer(status), RUN_NEW, RUN_OLD])
    expect(read('running').kind).toBe('newer')
    expect(read('running').text).toBe('Read only: a newer run still running, run #3 of 3')
    expect(read('pending').text).toContain('a newer run waiting to start')
    expect(read('failed').text).toContain('a newer run that failed')
    expect(read('cancelled').text).toContain('a newer run that was cancelled')
    expect(read('mystery').text).toContain('a newer run that has not completed')
    expect(read('failed').reason).toContain('has not completed')
  })

  it('names a run listed after the latest completed one as earlier, whatever its state', () => {
    const failed = { peak_assignment_run_id: 'run-0', status: 'failed' }
    const notice = runNotice(failed, RUN_NEW, [RUN_NEW, RUN_OLD, failed])
    expect(notice.kind).toBe('earlier')
    expect(notice.text).toBe('Read only: an earlier run, run #1 of 3')
    // A run the list does not hold cannot be placed: it reads as earlier.
    expect(runNotice(failed, RUN_NEW, [RUN_NEW]).kind).toBe('earlier')
  })
})
