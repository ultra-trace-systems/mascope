import { ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { getApiErrorMessage } from '@/api/utils'
import { focusSamplePeak } from '@/lib/panes/PaneBrowserSample/stores/focusSamplePeak.js'
import { useApp } from '@/stores'

/**
 * The brightest member of a batch peak's series: the sample it was measured
 * in and the peak to focus there.
 *
 * Pure. The series carries parallel arrays; a member without an intensity is
 * skipped, and the first of equal intensities wins so the choice is stable.
 *
 * @param {object|null|undefined} record - a series record
 *   (`POST /batch-peaks/records/series`)
 * @returns {{sample_item_id: string, sample_peak_id: string|null, intensity: number}|null}
 */
export function brightestMember(record) {
  const series = record?.peak_series
  const ids = series?.sample_item_ids ?? []
  const intensities = series?.intensities ?? []
  let best = -1
  ids.forEach((_, index) => {
    const intensity = intensities[index]
    if (intensity == null) return
    if (best === -1 || intensity > intensities[best]) best = index
  })
  if (best === -1) return null
  return {
    sample_item_id: ids[best],
    sample_peak_id: series.sample_peak_ids?.[best] ?? null,
    intensity: intensities[best]
  }
}

/**
 * Every measured member of a batch peak's series, brightest first: the
 * samples the inspector card steps through once the batch ledger has opened
 * the peak in its brightest one.
 *
 * Pure. A member without an intensity is not measured and is left out; equal
 * intensities keep the series' order, so the first is the one
 * `brightestMember` picks.
 *
 * @param {object|null|undefined} record - a series record
 * @returns {Array<{sample_item_id: string, sample_peak_id: string|null, intensity: number}>}
 */
export function measuredMembers(record) {
  const series = record?.peak_series
  const ids = series?.sample_item_ids ?? []
  const intensities = series?.intensities ?? []
  return ids
    .map((id, index) => ({
      sample_item_id: id,
      sample_peak_id: series.sample_peak_ids?.[index] ?? null,
      intensity: intensities[index]
    }))
    .filter((member) => member.intensity != null)
    .sort((a, b) => b.intensity - a.intensity)
}

/**
 * The batch ledger's row action: open the brightest sample that holds the
 * batch peak, with that peak focused, in the Sample tab.
 *
 * The chart offers the same click-through on a data point, but with several
 * traces plotted it is not obvious which point to click; the row knows its
 * own species, and the brightest sample is where it is best measured. One
 * series read for the one anchor - from the run on screen, as the chart reads
 * - then the shared click-through (`focusSamplePeak`).
 */
export const useBatchPeakJump = defineStore('browser.match.batchPeakJump', () => {
  const app = useApp()

  /** The batch peak whose jump is in flight, or null. */
  const pendingId = ref(null)

  /**
   * The batch peak the ledger last opened, for the inspector card: the batch
   * it is in, and its measured members among the loaded samples, brightest
   * first. The card names the sample it is showing and steps through the
   * others (`stepTo`). Null until a jump lands.
   */
  const opened = ref(null)

  const notify = (message, status = 'warning') =>
    app.ui.notification.push({ type: 'batch_peak_jump', message, status })

  async function jumpToBrightest(row) {
    if (pendingId.value) return
    const batchId = app.data.batch.focusedId
    if (!batchId || !row?.batch_peak_id) return
    pendingId.value = row.batch_peak_id
    try {
      const records = await api.http.post(
        '/batch-peaks/records/series',
        {
          sample_batch_id: batchId,
          batch_peak_ids: [row.batch_peak_id],
          // An earlier run's members come off its snapshot; the live ledger names no run.
          ...(app.data.batchPeakRun?.viewingId
            ? { batch_peak_run_id: app.data.batchPeakRun.viewingId }
            : {})
        },
        { use: 'read', type: 'load_batch_peak_series', errors: 'inline' }
      )
      const member = brightestMember(records?.[0])
      const sample =
        member && app.data.sample.list.find((s) => s.sample_item_id === member.sample_item_id)
      if (!sample) {
        notify('This batch peak has no measured member among the loaded samples.')
        return
      }
      // The samples the card steps through: the measured members the sample
      // list holds, brightest first, so the one opened here is the first.
      const loaded = new Set(app.data.sample.list.map((s) => s.sample_item_id))
      const members = measuredMembers(records?.[0]).filter((m) => loaded.has(m.sample_item_id))
      opened.value = { batch_peak_id: row.batch_peak_id, sample_batch_id: batchId, members }
      const outcome = await focusSamplePeak(app, sample, member.sample_peak_id)
      if (outcome === 'missing') {
        notify("The peak is not in the sample's loaded peak list, so only the sample was opened.")
      }
    } catch (err) {
      notify(getApiErrorMessage(err, 'Could not read where this batch peak is brightest.'), 'error')
    } finally {
      pendingId.value = null
    }
  }

  /**
   * Open another member of the batch peak the ledger opened: its sample, with
   * its peak in focus, as the jump opened the brightest.
   *
   * @param {number} index - the member's place in `opened.members`
   * @returns {Promise<string|null>} what `focusSamplePeak` focused, or null
   *   when there is no such member
   */
  async function stepTo(index) {
    const member = opened.value?.members?.[index]
    const sample =
      member && app.data.sample.list.find((s) => s.sample_item_id === member.sample_item_id)
    if (!sample) return null
    const outcome = await focusSamplePeak(app, sample, member.sample_peak_id)
    if (outcome === 'missing') {
      notify("The peak is not in the sample's loaded peak list, so only the sample was opened.")
    }
    return outcome
  }

  return { pendingId, opened, jumpToBrightest, stepTo }
})
