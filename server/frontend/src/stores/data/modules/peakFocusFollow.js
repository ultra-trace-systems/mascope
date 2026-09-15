/**
 * Carrying the focused peak across a sample switch.
 *
 * Switching samples used to drop the focused peak: the peak store reloads on a
 * dependency watcher and its refocus falls through to `unfocus()`, because the
 * old sample's peak ids match nothing in the new sample's list. Comparing one
 * compound across a batch therefore meant finding it again by m/z every time.
 *
 * "The same peak" is not an m/z guess here -- it is the batch-peak anchor, the
 * one identity a species already has across a batch. The backend walks it
 * (`GET /batch-peaks/records/counterpart`); this module owns the timing, which
 * is the whole difficulty.
 *
 * A factory over injected dependencies rather than a store, so the ordering
 * rules below can be tested without a component, a pinia, or a clock -- the
 * same shape `peakAssignment/detail.js` uses.
 */

/**
 * The counterpart's own peak id in the target sample, or null.
 *
 * Ids are compared as strings on both sides: the sample-peaks feed and the
 * occurrence table do not agree on the type (docs/dev/peak_assignment_frontend.md).
 *
 * @param {Array<object>|null} rows - what the counterpart read returned
 * @param {string} targetSampleItemId - the sample the counterpart must be in
 * @returns {string|null}
 */
export const counterpartSamplePeakId = (rows, targetSampleItemId) => {
  if (!Array.isArray(rows)) return null
  const row = rows.find(
    (candidate) =>
      candidate &&
      candidate.sample_peak_id != null &&
      String(candidate.sample_item_id) === String(targetSampleItemId)
  )
  return row ? String(row.sample_peak_id) : null
}

/**
 * Build the follower.
 *
 * `follow()` resolves the anchor and then waits for the peak store to hold the
 * target sample's peaks before writing anything. Two awaits means two chances
 * for the world to move underneath it, so every write is gated on the state it
 * assumed still holding.
 *
 * @param {object} deps
 * @param {(args: {sampleItemId: string, samplePeakId: string,
 *   targetSampleItemId: string}) => Promise<Array<object>>} deps.fetchCounterpart
 * @param {() => Promise<void>} deps.settled - resolves once the peak store has
 *   finished reloading (see `@/lib/store/settle`)
 * @param {{pending: boolean, error: *, focusedId: *, list: Array<object>,
 *   focus: (record: object) => void}} deps.peak
 * @param {{focusedId: *}} deps.sample
 * @param {() => number} deps.focusEpoch - a counter of peak-focus transitions;
 *   how a deliberate clear is told apart from the reload's own
 * @param {object} [deps.logger]
 */
export const createPeakFocusFollower = ({
  fetchCounterpart,
  settled,
  peak,
  sample,
  focusEpoch,
  logger
}) => {
  // Orders our own concurrent follows. Rapid switching leaves several in
  // flight and they settle in completion order, not issue order; only the
  // newest may write, or a slow lookup lands a peak from two samples ago.
  let generation = 0

  // Superseded lookups are left to complete and discarded rather than aborted.
  // An aborted request reaches the http interceptor with no response, which
  // logs a console error before it checks for inline errors -- so aborting
  // would print a red error per superseded switch for a silent background
  // feature. The heavier series fetch in ChartBatchAssignments does abort,
  // because there the connection it frees is worth the noise; this is one row.
  const follow = async ({ fromSampleItemId, fromPeakId, toSampleItemId }) => {
    const token = ++generation
    // Read before the first await: the reload's own unfocus is the one focus
    // transition this follow expects to see happen behind it.
    const epoch = focusEpoch()

    let rows
    try {
      rows = await fetchCounterpart({
        sampleItemId: fromSampleItemId,
        samplePeakId: fromPeakId,
        targetSampleItemId: toSampleItemId
      })
    } catch {
      // A failed lookup degrades to the old behaviour -- no focus, no message.
      // The request is sent with inline errors so nothing is announced.
      return false
    }

    if (token !== generation) return false

    const counterpartId = counterpartSamplePeakId(rows, toSampleItemId)
    if (!counterpartId) return false

    await settled()

    // A newer switch owns the focus now; this answer is about a sample the
    // user has already left.
    if (token !== generation) return false
    // Something we do not drive moved the sample while we waited: a sample
    // re-sync dropping its focus, a shared-link restore, the sample table.
    if (sample.focusedId !== toSampleItemId) return false
    // Still pending means the settle backstop fired rather than the reload
    // finishing, so the list is of unknown vintage. On a failed sync the
    // loader deliberately KEEPS the previous sample's rows, so an error means
    // the same thing -- belt and braces with the epoch check below, which
    // would also stand down because that path never unfocuses.
    if (peak.pending || peak.error) return false
    // The cheap half of "user intent wins": somebody already put a peak in
    // the slot we were going to fill.
    if (peak.focusedId != null) return false
    // And the half a state test cannot see. Exactly one focus transition is
    // expected behind us -- the reload's unfocus, or none when focus was
    // already empty. More than that means a human focused a peak and cleared
    // it again while we were waiting, and the empty selection is their choice,
    // not a vacancy.
    if (focusEpoch() > epoch + 1) return false

    const record = peak.list.find(
      (candidate) => String(candidate.peak_id) === String(counterpartId)
    )
    // Focus only a record we actually hold: focusing by a bare id that is not
    // in the list CLEARS the focus rather than leaving it alone.
    if (!record) return false

    logger?.debug(`following focus into ${toSampleItemId}`, {
      icon: '🔍',
      data: { from: fromPeakId, to: counterpartId }
    })
    peak.focus(record)
    return true
  }

  /** Stand down every follow in flight. */
  const cancel = () => {
    generation += 1
  }

  return { follow, cancel }
}
