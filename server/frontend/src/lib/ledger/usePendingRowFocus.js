import { ref, watch } from 'vue'

/**
 * A ledger row clicked before the sample's peak list is in.
 *
 * A row's click focuses its peak, found by id in the sample's peak list. The
 * ledger's rows can show before that list does, and a click made then found
 * nothing and did nothing at all (#3588). Such a click is kept here as pending
 * instead, and carried out once the list lands for the same sample.
 *
 * The pending click is dropped when the sample changes before the list lands,
 * when the list fails to load, when the peak is not in the list after all (a
 * row of a run from before the peaks were found again, which the ledger dims),
 * and when another click, or a deselect, replaces it.
 *
 * @param {object} deps
 * @param {() => boolean} deps.loading - the sample's peak list is loading
 * @param {() => boolean} deps.failed - the last load of that list failed
 * @param {() => *} deps.sampleId - the focused sample
 * @param {(peakId: *) => object|null} deps.find - the peak of that id in the
 *   list, or null
 * @param {(peak: object) => void} deps.focus - focus a peak of the list
 */
export function usePendingRowFocus({ loading, failed, sampleId, find, focus }) {
  // { peakId, sampleId, then } of the click waiting for the list, or null.
  const pending = ref(null)

  const clear = () => (pending.value = null)

  /**
   * Focus the peak of this id, now or once the list is in.
   *
   * @param {*} peakId - the row's `sample_peak_id`
   * @param {Function} [then] - run once the peak is focused
   * @returns {boolean} the peak was focused now
   */
  function request(peakId, then = null) {
    if (!loading()) {
      const peak = find(peakId)
      if (peak) {
        focus(peak)
        then?.()
      }
      return Boolean(peak)
    }
    pending.value = { peakId, sampleId: sampleId(), then }
    return false
  }

  // A click is kept only while the list loads, so with one waiting this fires
  // on the list landing or on the sample changing, and either settles it.
  watch([loading, sampleId], ([, sample]) => {
    const waiting = pending.value
    if (!waiting) return
    clear()
    if (String(sample) !== String(waiting.sampleId)) return
    if (failed()) return
    const peak = find(waiting.peakId)
    if (!peak) return
    focus(peak)
    waiting.then?.()
  })

  return { pending, request, clear }
}
