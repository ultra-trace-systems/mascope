/**
 * Presentation model for a sample's peak-assignment status in the sample
 * browser, beside the match and calibration badges.
 *
 * The record is one row of `GET /api/batch-peaks/batch/{id}/sample-status`
 * (the `batchPeakSampleStatus` store): the sample's latest completed
 * assignment run of its own, if any, and what the batch ledger holds for it.
 * Two things answer "has this sample been assigned", and they are not the
 * same: a run of its own - an explicit run, or a published external one - is
 * the deep dive; a sample folded into the batch ledger is served from it even
 * without a run. The badge tells them apart.
 */

const ICON = 'ph ph-tag'

const ledgerPhrase = (record) =>
  record.n_members
    ? `${record.n_assigned} of ${record.n_members} peak${record.n_members === 1 ? '' : 's'} ` +
      'assigned in the batch ledger'
    : 'not in the batch ledger'

/**
 * Derive the assignment badge for a sample row.
 *
 * @param {object|null|undefined} record - the sample's status record, or
 *   nothing while the status has not loaded
 * @returns {{state: 'run'|'ledger'|'none'|'unknown', icon: string, tooltip: string}}
 *   `run`: the sample has a completed run of its own; `ledger`: no run, but
 *   the batch ledger carries assignments for it; `none`: nothing assigned;
 *   `unknown`: the status has not loaded.
 */
export function assignmentStatus(record) {
  if (!record) {
    return { state: 'unknown', icon: ICON, tooltip: 'Assignment status not loaded yet' }
  }
  const ledger = ledgerPhrase(record)
  if (record.run) {
    const stamp = record.run.peak_assignment_run_utc_created
    const when = stamp ? ` on ${new Date(stamp).toLocaleString()}` : ''
    return {
      state: 'run',
      icon: ICON,
      tooltip:
        `Assigned by ${record.run.engine} ${record.run.engine_version}${when} ` +
        `(a run of its own); ${ledger}`
    }
  }
  if (record.n_assigned) {
    return {
      state: 'ledger',
      icon: ICON,
      tooltip: `Served from the batch ledger, no run of its own: ${ledger}`
    }
  }
  return {
    state: 'none',
    icon: ICON,
    tooltip: record.n_members ? `No assignments yet: ${ledger}` : 'No assignments: ' + ledger
  }
}
