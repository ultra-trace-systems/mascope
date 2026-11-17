// What a run's status says in the run selectors, declared once.
//
// The values are the API's: a sample's assignment run is 'pending', 'running',
// 'importing', 'completed', 'failed' or 'cancelled' (PeakAssignmentRun in the
// backend's db/models.py), a batch run 'running', 'completed' or 'failed'
// (batch_runs.py). Both selectors printed them as they came. Most are words
// already and keep them; 'pending' is a run the server has created and no task
// has taken up yet, which a reader knows as queued.
const RUN_STATUS_LABELS = Object.freeze({
  pending: 'queued',
  running: 'running',
  importing: 'importing',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled'
})

/**
 * The word for a run's status. One this build does not know is shown as it
 * came rather than hidden: it is still the run's state.
 *
 * @param {string|null} status the status as stored on the run
 * @returns {string} its label, or '' for a run with none
 */
export const runStatusLabel = (status) => {
  if (status == null) return ''
  return Object.prototype.hasOwnProperty.call(RUN_STATUS_LABELS, status)
    ? RUN_STATUS_LABELS[status]
    : String(status)
}
