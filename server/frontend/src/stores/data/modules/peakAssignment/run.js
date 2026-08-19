import { computed, watch } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'
import { peakAssignmentEnabled } from '@/lib/features'

import { useSample } from '../sample'

// Peak assignment RUNS for the focused sample.
//
// One row per assignment run over the sample, newest first. The focused run is
// the one currently being viewed; usePeakAssignment reads its assignments. See
// docs/dev/peak_assignment_frontend.md.
export const usePeakAssignmentRun = defineStore('app.data.peakAssignment.run', () => {
  const name = 'peak_assignment_run'
  const key = 'peak_assignment_run_id'

  const data = useData(
    name,
    ({ sample_item_id }) => {
      if (!sample_item_id) return []
      return api.http.get(`/peak-assignments/sample/${sample_item_id}/runs`, {
        use: 'read',
        type: 'load_peak_assignment_runs'
      })
    },
    {
      key,
      // The feature is opt-in, so with the flag off the dependency is reported
      // unmet: the loader then skips the request entirely and nothing here ever
      // reaches the API, even though the backend routes are always registered.
      // This store is instantiated by useData() regardless of the flag, so the
      // gate has to live where the fetch is decided.
      deps: () => ({
        sample_item_id: peakAssignmentEnabled ? useSample().focusedId : null
      }),
      selection: true,
      // The backend emits peak_assignment_reload when a run finalizes (mirrors
      // match_reload); the useData events framework re-syncs the run list.
      events: ['peak_assignment_reload']
    }
  )

  // Runs are returned newest-first, so the first completed run is the latest.
  const latestCompleted = computed(
    () => data.list.value.find((run) => run.status === 'completed') ?? null
  )

  // Default the view to the latest completed run, and follow it whenever it
  // changes.
  //
  // Two cases need the focus moved. The run list holds only the focused
  // sample's runs, so a focused id absent from it is either empty or a stale
  // carry-over from the previous sample (selection persists) -- re-focus the
  // latest. And when a NEW run finishes, the whole view (ledger, tier strip,
  // spectrum colouring, inspector) would otherwise keep rendering the previous
  // run's assignments, so re-assigning looks like it did nothing; the run the
  // user just asked for is what they expect to see.
  //
  // An explicit pick of an older run of the same sample is left alone: the
  // latest completed run has not changed in that case, and the picked run is
  // still in the list.
  let followedRunId = null
  watch(
    [latestCompleted, () => data.list.value],
    ([run]) => {
      const focusedInList = data.list.value.some(
        (r) => r.peak_assignment_run_id === data.focusedId.value
      )
      if (!run) {
        followedRunId = null
        return
      }
      const isNewLatest = run.peak_assignment_run_id !== followedRunId
      followedRunId = run.peak_assignment_run_id
      if (isNewLatest || !focusedInList) data.focus(run)
    },
    { immediate: true }
  )

  // Launch a new assignment run. Resolves with the created run's id; completion
  // arrives via the peak_assignment_reload event (see events above), and the
  // progress bar is driven by the assign_sample_peaks user notification
  // (PaneProgress) meanwhile.
  //
  // The endpoint decides the outcome before it answers, so a sample that is
  // already being assigned (409) or cannot usefully be assigned (422) rejects
  // with the server's reason. `errors: 'inline'` keeps the generic failure toast
  // off it - the launcher shows that reason where the user asked for the run.
  const assign = (sampleItemId, config = null) =>
    api.http.post(
      `/peak-assignments/sample/${sampleItemId}/assign`,
      { config },
      {
        use: 'process',
        type: 'assign_sample_peaks',
        errors: 'inline'
      }
    )

  return {
    ...data,
    latestCompleted,
    assign
  }
})
