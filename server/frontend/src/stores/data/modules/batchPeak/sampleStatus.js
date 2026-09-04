import { computed } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'

import { useBatch } from '../batch'

/**
 * Each sample's assignment status within the focused batch: its latest
 * completed assignment run of its own, if any, and what the batch ledger
 * holds for it (members, and how many carry an assignment). One read per
 * batch; the sample browser reads it per row through `forSample`, and
 * `lib/assignmentStatus.js` turns a record into the badge.
 *
 * Reloads with the ledger (`peak_assignment_reload`): a fold, a rebuild, a
 * search or an import changes what the ledger holds for a sample, and a run
 * completing changes whether it has one of its own.
 */
export const useBatchPeakSampleStatus = defineStore('app.data.batchPeak.sampleStatus', () => {
  const name = 'batch_peak_sample_status'
  const key = 'sample_item_id'

  const data = useData(
    name,
    ({ sample_batch_id }) => {
      if (!sample_batch_id) return []
      return api.http.get(`/batch-peaks/batch/${sample_batch_id}/sample-status`, {
        use: 'read',
        type: 'load_batch_peak_sample_status'
      })
    },
    {
      key,
      deps: () => ({ sample_batch_id: useBatch().focusedId }),
      events: ['peak_assignment_reload']
    }
  )

  const bySample = computed(
    () => new Map(data.list.value.map((record) => [record.sample_item_id, record]))
  )

  /** The status record of one sample, or null while none is loaded. */
  const forSample = (sampleItemId) => bySample.value.get(sampleItemId) ?? null

  return { ...data, forSample }
})
