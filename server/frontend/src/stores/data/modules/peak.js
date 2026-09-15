import { ref, watch } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'

import { peakAssignmentEnabled } from '@/lib/features'
import { makeLogger } from '@/lib/logging'
import { useData } from '@/lib/store'
import { untilStoreSettled } from '@/lib/store/settle'

import { useBatch } from './batch'
import { useBatchPeakLedger } from './batchPeak'
import { createPeakFocusFollower } from './peakFocusFollow'
import { useSample } from './sample'

export const usePeak = defineStore('app.data.peak', () => {
  const name = 'peak'
  const key = 'peak_id'

  const data = useData(
    name,
    async ({ sample_item_id }) => {
      if (!sample_item_id) {
        return []
      }
      const data = await api.http.get(`/samples/${sample_item_id}/peaks`, {
        params: {
          areas: true,
          heights: true,
          matches: true
        },
        use: 'read',
        type: 'load_sample_peaks'
      })
      if (data) {
        const { peak_id, mz, area, height, match } = data
        const records = mz.map((mz, i) => ({
          mz: mz,
          peak_id: peak_id[i],
          area: area[i],
          height: height[i],
          match: match[i]
        }))
        return records
      } else {
        return []
      }
    },
    {
      key,
      deps: () => ({
        sample_item_id: useSample().focusedId
      }),
      selection: { persist: true }
    }
  )

  // Focus follows the peak across a sample switch (see ./peakFocusFollow).
  //
  // Off entirely without the peak-assignment feature: batch peaks are what
  // define "the same peak", and with the feature off none are computed, so the
  // watchers would only ever cost a lookup that cannot answer.
  if (peakAssignmentEnabled) {
    const sample = useSample()
    const batch = useBatch()
    // Resolved here rather than in the watcher: this store is created after the
    // batch-peak ledger, so taking the reference now keeps the ordering an
    // explicit fact instead of an incidental one.
    const ledger = useBatchPeakLedger()
    const logger = makeLogger({ prefix: 'peak focus follow', icon: '🔍' })

    // Which sample the focused peak belongs to, and which batch that sample was
    // in. NOT reconstructible from the sample watcher's `prev`: through a burst
    // of switches the focused peak still belongs to the sample it was focused
    // in, several switches back, because the reload only clears it once its
    // fetch resolves. Pairing that peak with the sample merely being left would
    // ask the backend for a peak in a sample that never held it.
    const anchor = ref(null)
    // Counts peak-focus transitions so a follow can tell the reload's own
    // unfocus from a person clearing the selection while it waited.
    let focusEpoch = 0

    const follower = createPeakFocusFollower({
      fetchCounterpart: ({ sampleItemId, samplePeakId, targetSampleItemId }) =>
        api.http.get('/batch-peaks/records/counterpart', {
          params: {
            sample_item_id: sampleItemId,
            sample_peak_id: samplePeakId,
            target_sample_item_id: targetSampleItemId
          },
          use: 'read',
          type: 'load_batch_peak_counterpart',
          // Nothing about this read is worth interrupting the user for: a
          // failure just leaves the selection empty, which is where a sample
          // switch left it anyway.
          errors: 'inline'
        }),
      settled: () => untilStoreSettled(() => data.pending.value),
      peak: {
        get pending() {
          return data.pending.value
        },
        get error() {
          return data.error.value
        },
        get focusedId() {
          return data.focusedId.value
        },
        get list() {
          return data.list.value
        },
        focus: data.focus
      },
      sample: {
        get focusedId() {
          return sample.focusedId
        }
      },
      focusEpoch: () => focusEpoch,
      logger
    })

    watch(
      () => data.focusedId.value,
      (peakId) => {
        focusEpoch += 1
        if (peakId) {
          anchor.value = {
            sampleItemId: sample.focusedId,
            sampleBatchId: batch.focusedId,
            peakId
          }
        } else if (!data.pending.value || anchor.value?.sampleItemId === sample.focusedId) {
          // A person cleared it. Forget the anchor and abandon any follow still
          // in flight, or the selection they just emptied fills itself back in.
          //
          // Two ways to be sure it was not the reload's own clear, which has to
          // leave the anchor alone -- keeping one through it is the entire point.
          // Nothing was loading; or the anchor's sample is still the focused one,
          // which a sample switch cannot be, because it moves `sample.focusedId`
          // long before its reload gets to unfocus. `pending` alone is not enough:
          // it is true for EVERY peak-store sync, including a retry or a socket
          // reload inside one sample, and the ledger stays clickable through those.
          anchor.value = null
          follower.cancel()
        }
      },
      // Sync, because the reload clears the focus while `pending` is still true
      // and drops the flag in the same block. A deferred callback would read it
      // already false and mistake the reload for a person.
      { flush: 'sync' }
    )

    // Leaving the batch retires the anchor rather than merely suspending it.
    // The guard in the sample watcher below only suppresses follows while
    // another batch is open, and the cascade out of a batch clears the peak
    // focus from inside a sync, so the clear above does not fire either -- the
    // anchor would sit there through the whole excursion and come back to life
    // on returning, filling a selection that had been empty all along.
    watch(
      () => batch.focusedId,
      (next) => {
        if (anchor.value && anchor.value.sampleBatchId !== next) {
          anchor.value = null
          follower.cancel()
        }
      }
    )

    watch(
      () => sample.focusedId,
      (next, prev) => {
        // Only a switch from one sample to another. A null on either side is a
        // cold start, a shared-link restore, or a batch/dataset/workspace
        // cascade unwinding -- none of them a request to carry a peak along.
        if (!next || !prev) return
        const from = anchor.value
        if (!from?.peakId) return
        // The anchor's own sample is a legitimate target, not a no-op: coming
        // back to it after a sample that had no counterpart means the focus was
        // cleared on the way out, and the peak is sitting right there in the
        // list. A same-sample lookup resolves the peak to its own occurrence.
        //
        // Belt and braces after the batch watcher above, which is what actually
        // retires an anchor from a batch that is no longer loaded.
        if (from.sampleBatchId !== batch.focusedId) return
        // "Batch peaks computed": a ledger that finished loading with nothing
        // in it means the fold never ran for this batch, so no lookup can
        // answer. A ledger that failed to load says nothing either way, so the
        // backend gets to answer instead of the feature going quiet for the
        // session.
        if (!ledger.pending && !ledger.error && ledger.list.length === 0) return
        follower.follow({
          fromSampleItemId: from.sampleItemId,
          fromPeakId: from.peakId,
          toSampleItemId: next
        })
      }
    )
  }

  return {
    ...data,
    // api
    computeAll: ({ sample_file_id }) =>
      api.http.post(
        `/sample/files/${sample_file_id}/peaks/compute`,
        {},
        {
          use: 'read',
          type: 'compute_all_sample_peaks'
        }
      )
  }
})
