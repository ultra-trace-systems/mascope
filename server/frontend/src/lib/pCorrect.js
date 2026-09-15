/**
 * What the P(correct) column and the inspector's P(correct) row say about a
 * calibrated probability, and about its absence - one list, so the ledger's
 * cells and the inspector's row cannot drift apart on the same fact.
 */

/** The header's one line. What a dash means is on the dash, not here. */
export const P_CORRECT_TOOLTIP = 'Calibrated probability the assignment is correct'

/**
 * Why a row shows no calibrated probability. Several different reasons, and the
 * wrong one is worse than none: a hand-assigned row has no P(correct) because
 * nobody calibrated the formula a person chose, which says nothing about whether
 * this instrument has a curve - and reading "no calibration curve for this
 * instrument" there would send someone to calibrate an instrument that is
 * calibrated perfectly well.
 */
export const UNCALIBRATED_REASONS = Object.freeze({
  // First because a demoted satellite is both: curation strips the satellites of
  // a formula their M0 no longer holds and leaves source = 'manual' on them, so
  // a row can be a person's doing and hold no formula at all. "Assigned by
  // hand" on a row the tier chip beside it labels Unassigned is the ledger's
  // copy contradicting itself.
  unassigned: 'Nothing assigned to this peak',
  manual: 'Assigned by hand - the calibration never scored this formula',
  untargeted: 'Untargeted assignment - no calibrated probability',
  uncalibrated: 'No calibration curve for this instrument',
  // A row served from the batch ledger carries the probability recorded when the
  // sample was folded in, or none: the ledger does not score a sample itself, so
  // the instrument's calibration is not something it can be blamed for.
  ledger: 'No calibrated probability was recorded when this sample was folded into the batch ledger'
})

/**
 * The reason a row shows no P(correct).
 *
 * @param {object} row - a ledger row: `assigned_formula`, `source`
 * @param {{ fromLedger?: boolean }} [options] - `fromLedger` says the row is
 *   served from the batch ledger rather than from a run of the sample's own,
 *   where "no calibration curve" is not a thing the ledger could know
 * @returns {string}
 */
export function uncalibratedReason(row, { fromLedger = false } = {}) {
  if (!row?.assigned_formula) return UNCALIBRATED_REASONS.unassigned
  if (row.source === 'manual') return UNCALIBRATED_REASONS.manual
  if (row.source === 'untargeted') return UNCALIBRATED_REASONS.untargeted
  return fromLedger ? UNCALIBRATED_REASONS.ledger : UNCALIBRATED_REASONS.uncalibrated
}

/**
 * The tooltip on a P(correct) the sample did not compute for itself. A row
 * served from the batch ledger shows the probability recorded when the sample
 * was folded in: calibrated on this sample's own peak at that time, by the
 * ingest-time assignment or a run since removed, and not re-scored since.
 */
export const LEDGER_P_CORRECT_TOOLTIP =
  'As recorded in the batch ledger when this sample was folded in: calibrated on this ' +
  "sample's own peak at that time, and served from the ledger since rather than re-scored"

/**
 * Why a row served from the batch ledger shows no arbitration confidence: that
 * number is a run's, from weighing a peak's candidates against each other, and
 * a ledger member carries one identity with nothing to weigh it against.
 */
export const LEDGER_CONFIDENCE_TOOLTIP =
  'Arbitration confidence comes from an assignment run of this sample, which weighs the ' +
  "peak's candidates against each other; a row served from the batch ledger carries one " +
  'identity and no such contest'
