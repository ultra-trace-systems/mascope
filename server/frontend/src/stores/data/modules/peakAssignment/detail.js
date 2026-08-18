import { reactive } from 'vue'

/**
 * Per-assignment detail loader for the slim assignment ledger.
 *
 * The list endpoint serves a slim projection without `alternatives` /
 * `provenance` (they are ~74% of a full row's bytes and only the inspector
 * reads them); the full record is fetched per assignment when a peak is
 * focused. Records are cached by peak_assignment_id, concurrent loads of the
 * same assignment share one request, and `clear()` drops everything when the
 * ledger reloads (a re-run creates new assignment ids, so cached detail is
 * stale the moment the list changes).
 *
 * A list row that already carries the detail fields (a backend predating the
 * slim projection) is used as-is, so nothing extra is fetched from servers
 * that still send full rows.
 *
 * @param {(assignment: object) => Promise<object|null>} fetchDetail - fetches
 *   the full record for a slim ledger row.
 */
export function createDetailLoader(fetchDetail) {
  const records = reactive(new Map()) // peak_assignment_id -> full record
  const inFlight = new Map() // peak_assignment_id -> pending request

  /** Cached full record for an assignment id, or null while not loaded. */
  const detailOf = (peakAssignmentId) =>
    peakAssignmentId == null ? null : (records.get(peakAssignmentId) ?? null)

  /** Ensure the full record for a ledger row is loaded; resolves to it. */
  async function loadDetail(assignment) {
    const id = assignment?.peak_assignment_id
    if (id == null) return null
    if (records.has(id)) return records.get(id)
    if (assignment.alternatives !== undefined || assignment.provenance !== undefined) {
      // Pre-slim row: the detail is already on it.
      records.set(id, assignment)
      return assignment
    }
    if (inFlight.has(id)) return inFlight.get(id)
    const request = (async () => {
      try {
        const record = await fetchDetail(assignment)
        if (record) records.set(id, record)
        return record ?? null
      } finally {
        inFlight.delete(id)
      }
    })()
    inFlight.set(id, request)
    return request
  }

  const clear = () => {
    records.clear()
    inFlight.clear()
  }

  return { detailOf, loadDetail, clear }
}
