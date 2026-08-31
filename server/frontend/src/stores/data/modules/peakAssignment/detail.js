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

/**
 * Per-assignment lazy cache for something computed rather than stored.
 *
 * Same caching contract as `createDetailLoader` - one request per id, shared by
 * concurrent callers, dropped wholesale by `clear()` - without its "the row
 * already carries this" short-circuit, which only makes sense for fields the
 * list endpoint may have sent. A computed result is never on the row.
 *
 * @param {(assignment: object) => Promise<any>} compute - fetches the result
 *   for one assignment.
 */
export function createComputedLoader(compute) {
  const records = reactive(new Map()) // peak_assignment_id -> result
  const inFlight = new Map() // peak_assignment_id -> pending request
  // The ids of `inFlight`, separately and reactively, so a view can render
  // "working on it". The promises themselves stay in a plain Map: a reactive
  // one would hand out a proxied promise, which is not what an awaiting caller
  // asked for.
  const pending = reactive(new Set())

  /** Cached result for an assignment id, or null while not loaded. */
  const resultOf = (peakAssignmentId) =>
    peakAssignmentId == null ? null : (records.get(peakAssignmentId) ?? null)

  /** Whether a load for this id is still in flight. */
  const pendingFor = (peakAssignmentId) =>
    peakAssignmentId != null && pending.has(peakAssignmentId)

  /** Ensure the result for a row is loaded; resolves to it. */
  async function load(assignment) {
    const id = assignment?.peak_assignment_id
    if (id == null) return null
    if (records.has(id)) return records.get(id)
    if (inFlight.has(id)) return inFlight.get(id)
    const request = (async () => {
      try {
        const result = await compute(assignment)
        if (result != null) records.set(id, result)
        return result ?? null
      } finally {
        inFlight.delete(id)
        pending.delete(id)
      }
    })()
    inFlight.set(id, request)
    pending.add(id)
    return request
  }

  const clear = () => {
    records.clear()
    inFlight.clear()
    pending.clear()
  }

  return { resultOf, pendingFor, load, clear }
}
