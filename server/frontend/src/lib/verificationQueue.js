// The verification queue: the assignments of a run worth a verdict next, as the
// server ranks them (peak_assignments/verification_queue.py, MAS-329). Pure
// metadata and joins - no store state.
//
// The queue is guardrail 3 of the verification loop
// (docs/dev/verification_calibration_loop.md): verdicts are the labels the
// confidence curve is refit from, and a verdict on a row far from a tier edge is
// one anybody could predict. So the queue offers the rows near an edge, where
// the instrument holds few labels, with random spot checks between them, and
// first the verdicts a later run read differently. An entry carries no
// probability and no evidence: what it says is why the row is there.

import { VERDICT_META } from '@/lib/verification'

// reason -> what the ledger's queue column shows.
export const QUEUE_REASONS = Object.freeze({
  stale: { label: 'Judge again', icon: 'ph ph-clock-counter-clockwise' },
  band_edge: { label: 'Near a tier edge', icon: 'ph ph-arrows-in-line-horizontal' },
  spot_check: { label: 'Spot check', icon: 'ph ph-shuffle' }
})

export const queueReason = (entry) => QUEUE_REASONS[entry?.reason] ?? null

const verdictWord = (verdict) =>
  (VERDICT_META[verdict]?.label ?? verdict ?? 'earlier').toLowerCase()

/**
 * What the queue column's hover says about one entry: why the row is asked.
 *
 * @param {Object} entry a queue entry
 * @returns {string}
 */
export function queueTooltip(entry) {
  if (!entry) return ''
  if (entry.reason === 'stale') {
    const stale = entry.stale ?? {}
    const verdict = verdictWord(stale.verdict)
    if (stale.kind === 'reassigned') {
      return (
        `A ${verdict} verdict on ${stale.assigned_formula ?? 'another formula'} was given ` +
        'before this run, which assigns this peak another formula. That verdict stays; ' +
        'judge what the run reads now.'
      )
    }
    return (
      `The ${verdict} verdict was given before this run, which reads this assignment ` +
      'with other evidence. It counts at its old score until you judge the row again; ' +
      'a new verdict replaces it.'
    )
  }
  if (entry.reason === 'band_edge') {
    const held =
      entry.labels_near === 0
        ? 'no verdict yet'
        : `${entry.labels_near} ${entry.labels_near === 1 ? 'verdict' : 'verdicts'} so far`
    const band = entry.near_band === 'candidate' ? 'candidate' : 'assigned'
    return (
      `Near the edge of the ${band} tier, where the instrument holds ${held} at evidence ` +
      'like this one. A verdict here teaches the confidence curve the most.'
    )
  }
  if (entry.reason === 'spot_check') {
    return 'A random spot check, drawn from the rows far from the tier edges.'
  }
  return ''
}

/**
 * The ledger's rows in the queue's order, each carrying its entry.
 *
 * A row the queue does not name is left out, and an entry whose row the loaded
 * run does not hold is skipped: the two are read apart, and a verdict recorded
 * in between can leave either one behind for a moment.
 *
 * @param {Array<Object>} rows the ledger's rows
 * @param {Array<Object>} entries the queue, in order
 * @returns {Array<Object>} `{ ...row, queueEntry, queueRank }`, in queue order
 */
export function queueRows(rows, entries) {
  const byId = new Map((rows ?? []).map((row) => [row.peak_assignment_id, row]))
  const out = []
  for (const entry of entries ?? []) {
    const row = byId.get(entry.peak_assignment_id)
    if (row) out.push({ ...row, queueEntry: entry, queueRank: out.length })
  }
  return out
}
