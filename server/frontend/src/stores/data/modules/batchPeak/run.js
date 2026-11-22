import { computed, watch } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { runStatusLabel } from '@/lib/runStatus'
import { useData } from '@/lib/store'

import { useBatch } from '../batch'

/**
 * The batch stages in the order every pass runs them, with their labels, as
 * the backend's `batch_stages.STAGES` names them. A pass's summary and the
 * status read carry the labels themselves; this is what a run that carries
 * none (a pass still running, a run from before the passes) is named by.
 */
export const STAGE_LABELS = Object.freeze({
  water_ladders: 'Measure water ladders',
  known_species: 'Decide known species',
  search: 'Search untargeted',
  isotope_lines: 'Isotope lines',
  series: 'Detect series',
  co_variation: 'Read co-variation',
  continuity: 'Ratio stability',
  batch_checks: 'Read batch checks',
  blends: 'Probable blends'
})

/** The fold of every sample a pass may start with: no stage of the list. */
export const FOLD_STAGE = 'fold'

/** The stage a step's own route starts its pass at, by the run's action. */
const ACTION_STAGES = Object.freeze({
  measure_water_ladders: 'water_ladders',
  decide_known_species: 'known_species',
  search_untargeted: 'search',
  strip_isotopologues: 'isotope_lines',
  stamp_isotopologues: 'isotope_lines',
  detect_series: 'series',
  read_co_variation: 'co_variation',
  read_batch_checks: 'batch_checks'
})

/** The runs that are no pass, under the labels they always had. */
const OTHER_LABELS = Object.freeze({
  fold: 'Folded samples',
  rebuild: 'Rebuild',
  import: 'Import'
})

const FIRST_STAGE = Object.keys(STAGE_LABELS)[0]

/** A stage's label, from the run's own summary where it names it. */
function stageLabelOf(run, stage) {
  const entries = Array.isArray(run?.summary?.stages) ? run.summary.stages : []
  return entries.find((entry) => entry?.stage === stage)?.label ?? STAGE_LABELS[stage] ?? stage
}

/** The stage a pass started at, where its record says. */
export function firstStageOf(run) {
  return run?.summary?.first_stage ?? run?.config?.from_stage ?? null
}

/**
 * What Run history calls a batch run (docs/dev/batch_assignment_one_action.md,
 * 3.2): a pass is an *Assignment*, or *Assignment from a stage (...)* where it
 * started past the first stage; a stage run alone through its own route keeps
 * its stage's name; imports and the folds keep their labels.
 */
export function runLabel(run) {
  if (!run) return ''
  const action = run.action
  if (action === 'assign') {
    const first = firstStageOf(run)
    if (!first || first === FOLD_STAGE || first === FIRST_STAGE) return 'Assignment'
    return `Assignment from a stage (${stageLabelOf(run, first)})`
  }
  if (ACTION_STAGES[action]) return stageLabelOf(run, ACTION_STAGES[action])
  return OTHER_LABELS[action] ?? action ?? ''
}

/** Where a run stands, in Run history's words: current, or its status. */
export function runStateWords(run) {
  if (!run) return ''
  if (run.current) return 'current'
  const label = runStatusLabel(run.status)
  return run.status === 'running' ? `${label}…` : label
}

/**
 * A batch's runs: its ledger's history, one record per batch-level operation
 * that rewrote it (a rebuild, an untargeted search with its parameters, an
 * import), or the folds that built it. Exactly one is current - the run whose
 * state the live ledger holds - and any earlier one can be viewed as it left
 * the ledger, read-only, from its snapshot.
 *
 * Follows the current run the way the per-sample runs store follows the latest
 * completed one: when a new run completes, the view moves to it, unless the
 * user is looking at another run that is still listed.
 */
export const useBatchPeakRun = defineStore('app.data.batchPeak.run', () => {
  const name = 'batch_peak_run'
  const key = 'batch_peak_run_id'

  const data = useData(
    name,
    ({ sample_batch_id }) => {
      if (!sample_batch_id) return []
      return api.http.get(`/batch-peaks/batch/${sample_batch_id}/runs`, {
        use: 'read',
        type: 'load_batch_peak_runs'
      })
    },
    {
      key,
      deps: () => ({
        // Gated by the batch alone, as the ledger this decorates is: the
        // ledger store imports this one, so a feature-flag read here would
        // pull the runtime config into every consumer of the ledger.
        sample_batch_id: useBatch().focusedId
      }),
      selection: true,
      events: ['peak_assignment_reload']
    }
  )

  const current = computed(() => data.list.value.find((run) => run.current) ?? null)

  /** A run's number in the batch's history, the oldest kept being #1; null when not listed. */
  function ordinal(run) {
    const list = data.list.value
    const index = list.findIndex((r) => r.batch_peak_run_id === run?.batch_peak_run_id)
    return index === -1 ? null : list.length - index
  }

  /** Show the live ledger again, after an earlier run's. */
  function backToCurrent() {
    if (current.value) data.focus(current.value)
    else data.unfocus()
  }

  let followedRunId = null
  watch(
    [current, () => data.list.value],
    ([run]) => {
      const focusedInList = data.list.value.some(
        (r) => r.batch_peak_run_id === data.focusedId.value
      )
      if (!run) {
        followedRunId = null
        return
      }
      const isNewCurrent = run.batch_peak_run_id !== followedRunId
      followedRunId = run.batch_peak_run_id
      if (isNewCurrent || !focusedInList) data.focus(run)
    },
    { immediate: true }
  )

  /** The run whose ledger is on screen: the focused one, else the current. */
  const viewing = computed(() => data.focused.value ?? current.value)
  /** Whether the live ledger is on screen (nothing selected, or the current run). */
  const viewingCurrent = computed(() => !viewing.value || Boolean(viewing.value.current))
  /** The run id a read must name to get an earlier run's ledger; null for live. */
  const viewingId = computed(() =>
    viewingCurrent.value ? null : (viewing.value?.batch_peak_run_id ?? null)
  )

  return { ...data, current, viewing, viewingCurrent, viewingId, ordinal, backToCurrent }
})
