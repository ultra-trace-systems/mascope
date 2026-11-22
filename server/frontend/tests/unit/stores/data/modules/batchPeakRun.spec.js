import { describe, it, expect, vi, beforeEach } from 'vitest'
import { computed, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// The batch's runs: what Run history calls each, and the read-only state an
// earlier run puts on screen. The loader is stood in for by refs the test
// sets, as the listing would.

vi.mock('@/api', () => ({ api: { http: { get: vi.fn() } } }))

let list
let focused
vi.mock('@/lib/store', () => ({
  useData: () => ({
    list,
    focused,
    focusedId: computed(() => focused.value?.batch_peak_run_id ?? null),
    focus: (run) => (focused.value = run),
    unfocus: () => (focused.value = null)
  })
}))
vi.mock('@/stores/data/modules/batch', () => ({ useBatch: () => ({ focusedId: 'b-1' }) }))

const { useBatchPeakRun, runLabel, runStateWords } =
  await import('@/stores/data/modules/batchPeak/run.js')

const run = (id, action, over = {}) => ({
  batch_peak_run_id: id,
  action,
  status: 'completed',
  current: false,
  ...over
})

/** A pass's summary as `batch_stages.PassRecord` writes it, from `first`. */
const passSummary = (first, labels = {}) => ({
  first_stage: first,
  stages: [
    { stage: 'fold', label: 'Fold the samples', status: 'not_run' },
    { stage: 'water_ladders', label: 'Measure water ladders', status: 'not_run' },
    { stage: 'series', label: labels.series ?? 'Detect series', status: 'done' }
  ]
})

describe('runLabel', () => {
  it('calls a whole pass an Assignment, from the fold or from the first stage', () => {
    expect(runLabel(run('r1', 'assign', { summary: passSummary('fold') }))).toBe('Assignment')
    expect(runLabel(run('r2', 'assign', { summary: passSummary('water_ladders') }))).toBe(
      'Assignment'
    )
  })

  it('names the stage a pass started later at, in its own summary’s words', () => {
    expect(
      runLabel(
        run('r1', 'assign', { summary: passSummary('series', { series: 'Homologous series' }) })
      )
    ).toBe('Assignment from a stage (Homologous series)')
  })

  it('names a running pass that started later by what it was asked for', () => {
    const running = run('r1', 'assign', {
      status: 'running',
      summary: { progress: { stage: 'co_variation' } },
      config: { from_stage: 'co_variation' }
    })
    expect(runLabel(running)).toBe('Assignment from a stage (Read co-variation)')
  })

  it('calls a pass with no record of its start an Assignment', () => {
    expect(runLabel(run('r1', 'assign'))).toBe('Assignment')
  })

  it.each([
    ['search_untargeted', 'Search untargeted'],
    ['detect_series', 'Detect series'],
    ['read_co_variation', 'Read co-variation'],
    ['stamp_isotopologues', 'Isotope lines'],
    ['strip_isotopologues', 'Isotope lines'],
    ['read_batch_checks', 'Read batch checks'],
    ['decide_known_species', 'Decide known species'],
    ['measure_water_ladders', 'Measure water ladders']
  ])('names a stage run alone through its route, %s, by its stage', (action, label) => {
    expect(runLabel(run('r1', action))).toBe(label)
  })

  it.each([
    ['import', 'Import'],
    ['fold', 'Folded samples'],
    ['rebuild', 'Rebuild']
  ])('keeps the label of %s', (action, label) => {
    expect(runLabel(run('r1', action))).toBe(label)
  })

  it('shows an action this build does not know as it came', () => {
    expect(runLabel(run('r1', 'something_new'))).toBe('something_new')
  })
})

describe('runStateWords', () => {
  it('says current, the status in words, and an ellipsis while running', () => {
    expect(runStateWords(run('r1', 'assign', { current: true }))).toBe('current')
    expect(runStateWords(run('r1', 'assign', { status: 'cancelled' }))).toBe('cancelled')
    expect(runStateWords(run('r1', 'assign', { status: 'running' }))).toBe('running…')
  })
})

describe('useBatchPeakRun', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    list = ref([])
    focused = ref(null)
  })

  it('numbers the runs from the oldest kept', () => {
    list.value = [run('r3', 'assign', { current: true }), run('r2', 'rebuild'), run('r1', 'fold')]
    const runs = useBatchPeakRun()
    expect(runs.ordinal(list.value[0])).toBe(3)
    expect(runs.ordinal(list.value[2])).toBe(1)
    expect(runs.ordinal(run('gone', 'fold'))).toBeNull()
  })

  it('picking an earlier run sets the read-only state, and Back to current refocuses the current', async () => {
    list.value = [run('r3', 'assign', { current: true }), run('r2', 'assign')]
    const runs = useBatchPeakRun()
    await Promise.resolve()
    expect(runs.viewingCurrent).toBe(true)
    expect(runs.viewingId).toBeNull()

    runs.focus(list.value[1])
    expect(runs.viewingCurrent).toBe(false)
    expect(runs.viewingId).toBe('r2')

    runs.backToCurrent()
    expect(runs.focused.batch_peak_run_id).toBe('r3')
    expect(runs.viewingCurrent).toBe(true)
    expect(runs.viewingId).toBeNull()
  })

  it('Back to current with no current run shows the live ledger', () => {
    list.value = [run('r2', 'assign', { status: 'failed' })]
    const runs = useBatchPeakRun()
    runs.focus(list.value[0])
    expect(runs.viewingCurrent).toBe(false)

    runs.backToCurrent()
    expect(runs.focused).toBeNull()
    expect(runs.viewingCurrent).toBe(true)
  })
})
