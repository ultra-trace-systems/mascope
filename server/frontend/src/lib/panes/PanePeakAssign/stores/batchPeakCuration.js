import { ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { useApp } from '@/stores'

/**
 * Curating a batch peak from the inspector: "use this" on a derived row.
 *
 * A derived row has no assignment row of its own to edit, so the override goes
 * to the batch peak: the chosen identity is pinned as the species for the whole
 * batch and measured in every sample holding the peak. That is a background
 * task, so `curate()` resolves when the task's own notification lands (or
 * rejects when it reports an error), not when the request is acknowledged -
 * the inspector's button spins for as long as the work actually takes. The
 * task emits `peak_assignment_reload` on completion, which is what refreshes
 * the ledgers; this store reloads nothing itself.
 *
 * `release()` is synchronous on the server, so it reloads the sample's
 * assignments and the batch ledger before resolving.
 */
export const useBatchPeakCuration = defineStore('peakAssign.batchPeakCuration', () => {
  const app = useApp()
  // Bounded wait, as for the compute: a dropped socket must not strand the
  // button. Generous - the measurement walks every sample of the batch.
  const CURATE_TIMEOUT = 5 * 60 * 1000
  const curating = ref(false)
  let pending = null // { processId, resolve, reject, timer }

  function settle(error, notification) {
    if (!pending) return
    clearTimeout(pending.timer)
    const { resolve, reject } = pending
    pending = null
    curating.value = false
    if (error) reject(error)
    else resolve(notification ?? null)
  }

  /** Pin `body.candidate` on `body.batch_peak_id`; resolves when the task ends. */
  async function curate(body) {
    const batchId = app.data.batch.focusedId
    if (!batchId || curating.value) return null
    curating.value = true
    let response
    try {
      response = await api.http.post(`/batch-peaks/batch/${batchId}/curate`, body, {
        use: 'update',
        type: 'curate_batch_peak'
      })
    } catch (error) {
      curating.value = false
      throw error
    }
    return new Promise((resolve, reject) => {
      pending = {
        processId: response?.headers?.['process-id'] ?? null,
        resolve,
        reject,
        timer: setTimeout(
          () => settle(new Error('The curation did not report back in time.')),
          CURATE_TIMEOUT
        )
      }
    })
  }

  app.ui.notification.on('curate_batch_peak', (notification) => {
    if (!pending || notification?.status === 'pending') return
    // A packet we can identify as someone else's curation of this batch is not
    // ours to settle on; one we cannot identify is accepted rather than ignored.
    const id = notification?.process_id
    if (pending.processId && id && id !== pending.processId) return
    if (notification?.status === 'error') {
      settle(new Error(notification?.message ?? 'The curation failed.'))
    } else {
      settle(null, notification)
    }
  })

  /** Undo the pin on `body.batch_peak_id`, then refresh what shows it. */
  async function release(body) {
    const batchId = app.data.batch.focusedId
    if (!batchId) return null
    const response = await api.http.post(`/batch-peaks/batch/${batchId}/release-curation`, body, {
      use: 'update',
      type: 'release_batch_peak_curation'
    })
    await Promise.all([
      app.data.peakAssignment.peak.load('release curation'),
      app.data.batchPeak.load('release curation')
    ])
    return response?.data?.[0] ?? null
  }

  return { curating, curate, release }
})
