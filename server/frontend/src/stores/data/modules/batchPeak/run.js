import { computed, watch } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'

import { useBatch } from '../batch'

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

  return { ...data, current, viewing, viewingCurrent, viewingId }
})
