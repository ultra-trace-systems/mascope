import { computed } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'
import { peakAssignmentEnabled } from '@/lib/features'

import { useSample } from '../sample'
import { usePeakAssignmentRun } from './run'

// The VERIFICATION QUEUE of the focused run: its assignments worth a verdict
// next, in the order the server ranks them (see lib/verificationQueue.js). One
// entry per row, keyed by peak_assignment_id. Reloaded with the run, and by the
// verification store after a verdict, which takes the judged row out.
export const usePeakAssignmentQueue = defineStore('app.data.peakAssignment.queue', () => {
  const name = 'verification_queue'
  const key = 'peak_assignment_id'

  const data = useData(
    name,
    ({ sample_item_id, peak_assignment_run_id }) => {
      if (!sample_item_id || !peak_assignment_run_id) return []
      return api.http.get(`/peak-assignments/sample/${sample_item_id}/verification-queue`, {
        params: { peak_assignment_run_id },
        use: 'read',
        type: 'load_verification_queue'
      })
    },
    {
      key,
      // As the ledger's own store: the flag off, or a run that is not this
      // sample's or still importing, leaves the dependency unmet.
      deps: () => {
        if (!peakAssignmentEnabled) return { sample_item_id: null, peak_assignment_run_id: null }
        const sample_item_id = useSample().focusedId
        const run = usePeakAssignmentRun().focused
        const servable = run && run.status !== 'importing' && run.sample_item_id === sample_item_id
        return {
          sample_item_id,
          peak_assignment_run_id: servable ? run.peak_assignment_run_id : null
        }
      }
    }
  )

  const count = computed(() => data.list.value.length)

  return { ...data, count }
})
