import { computed, watch } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'
import { peakAssignmentEnabled } from '@/lib/features'

import { useSample } from '../sample'
import { usePeakAssignmentRun } from './run'
import { createDetailLoader } from './detail'

// Page size requested per call. A run has one row per detected peak, so a large
// sample runs to tens of thousands of rows; the endpoint pages them.
const PAGE_SIZE = 1000

// Hard stop on the paging loop. At PAGE_SIZE rows a page this covers any real
// run, and it guarantees termination whatever the server does with the params.
const MAX_PAGES = 200

/**
 * Fetch one page of assignments.
 *
 * Pages are slim ledger rows: the endpoint drops the `alternatives` /
 * `provenance` JSON (inspector-only detail, fetched per assignment on peak
 * selection via `loadDetail`).
 *
 * Deliberately bypasses the shared `read` handler, which unwraps the envelope
 * to its `data` array and so hides the `total` this loop needs to know when it
 * has the whole run.
 */
async function fetchAssignmentPage(sampleItemId, params) {
  const response = await api.http.get(`/peak-assignments/sample/${sampleItemId}`, {
    params,
    type: 'load_peak_assignments'
  })
  const body = response?.data ?? {}
  return { rows: body.data ?? [], total: body.total ?? null }
}

/**
 * Load every assignment of a run, page by page.
 *
 * Tolerates both response shapes: an unpaginated one (no `total`, the first
 * response is the whole run) and a paginated one. Pages advance by the number
 * of rows actually received rather than by the requested limit, so a server
 * that caps the page size lower than PAGE_SIZE still yields the full run, and
 * a page that repeats rows already seen (a server ignoring `offset`) ends the
 * loop instead of duplicating them.
 */
async function loadAssignments(sampleItemId, runId) {
  const rows = []
  const seen = new Set()
  let offset = 0

  for (let page = 0; page < MAX_PAGES; page++) {
    const { rows: received, total } = await fetchAssignmentPage(sampleItemId, {
      peak_assignment_run_id: runId,
      limit: PAGE_SIZE,
      offset
    })

    const fresh = received.filter((record) => !seen.has(String(record.sample_peak_id)))
    for (const record of fresh) seen.add(String(record.sample_peak_id))
    rows.push(...fresh)

    if (total == null) break
    if (received.length === 0 || fresh.length === 0 || rows.length >= total) break
    offset += received.length
  }

  return rows
}

/**
 * The M0 of a row's isotopologue family - the row itself unless it is a
 * isotopologue, in which case its owner.
 *
 * The M0 is what the whole family is *about*: an M+1 peak is not a separate
 * finding, it is the same compound seen through one heavy atom. Anything that
 * judges or labels a compound therefore anchors on the M0 rather than on
 * whichever family member happens to be focused.
 *
 * An isotopologue with no resolvable owner is its own anchor. This is ordinary, not
 * corruption: the engine leaves `owner_peak_assignment_id` null when the ion's
 * M0 peak was won by a different ion in that run, so the isotopologue is a real row
 * whose family simply has no M0 in the ledger. Returning null would make callers
 * treat it as no row at all - no badge, nothing to verify - which is a worse
 * answer than letting it stand for itself.
 *
 * @param {Object} assignment a ledger row, or null
 * @param {Map} byId peak_assignment_id -> row, for the loaded run
 * @returns {Object|null} the family's M0, the row itself when it has no owner in
 *   the ledger, or null when there is no row
 */
export function familyM0(assignment, byId) {
  if (!assignment) return null
  if (assignment.role !== 'iso_child') return assignment
  return byId?.get(assignment.owner_peak_assignment_id) ?? assignment
}

// Peak ASSIGNMENTS for the focused sample + focused run.
//
// One row per observed peak, keyed by sample_peak_id (unique within a run and
// the join key back to the peak list). Exposes byPeakId (consumed by the peak
// ledger and the annotated spectrum) and a tier histogram. See
// docs/dev/peak_assignment_frontend.md.
export const usePeakAssignment = defineStore('app.data.peakAssignment', () => {
  const name = 'peak_assignment'
  const key = 'sample_peak_id'

  const data = useData(
    name,
    ({ sample_item_id, peak_assignment_run_id }) => {
      if (!sample_item_id || !peak_assignment_run_id) return []
      return loadAssignments(sample_item_id, peak_assignment_run_id)
    },
    {
      key,
      deps: () => {
        // The feature is opt-in and this store is instantiated by useData()
        // regardless, so with the flag off report the dependency unmet: the
        // loader skips the request and nothing here reaches the API.
        if (!peakAssignmentEnabled) {
          return { sample_item_id: null, peak_assignment_run_id: null }
        }
        const sample_item_id = useSample().focusedId
        const run = usePeakAssignmentRun().focused
        // Guard against a stale run from the previously focused sample: only
        // fetch with a run id that belongs to the current sample. On a sample
        // switch the run store briefly still holds the old sample's run; using
        // it would 404. Null here (skip the fetch) until the run store
        // re-focuses this sample's latest run, then deps update and we load.
        // An import still assembling is offered in the run selector but its
        // ledger is not servable - the read refuses it, since the rows staged
        // so far are real while the set is not. Skip the fetch rather than
        // let the panel ask for rows the server will not give.
        const servable = run && run.status !== 'importing'
        const peak_assignment_run_id =
          servable && run.sample_item_id === sample_item_id ? run.peak_assignment_run_id : null
        return { sample_item_id, peak_assignment_run_id }
      },
      selection: true
    }
  )

  // Per-assignment detail (alternatives + provenance), fetched lazily when the
  // inspector focuses a peak; the ledger rows above are the slim projection.
  const detail = createDetailLoader(async (assignment) => {
    const response = await api.http.get(
      `/peak-assignments/sample/${assignment.sample_item_id}` +
        `/assignment/${assignment.peak_assignment_id}`,
      { type: 'load_peak_assignment_detail' }
    )
    return response?.data?.data?.[0] ?? null
  })

  // A reload (sample/run switch, re-run) replaces the rows and their
  // assignment ids, so any cached detail is stale with them.
  watch(data.list, () => detail.clear())

  // Run metadata for the current view. The shared `read` handler unwraps the
  // response to its `data` field, dropping the {run, data} envelope's `run`, so
  // we take run metadata from the run store instead of the assignments call.
  const run = computed(() => usePeakAssignmentRun().focused)

  // Peak-join map keyed by String(sample_peak_id): the peak list keys peaks by
  // `peak_id`, which the engine stringifies into `sample_peak_id`. Consumed by
  // ChartSampleSpectrum (tier coloring) and the peak inspector.
  const byPeakId = computed(() => {
    const map = new Map()
    for (const record of data.list.value) {
      map.set(String(record.sample_peak_id), record)
    }
    return map
  })

  // Look up the assignment for a peak by its peak_id.
  const forPeak = (peakId) => (peakId == null ? null : (byPeakId.value.get(String(peakId)) ?? null))

  // Map peak_assignment_id -> record, for owner/child lookups.
  const byId = computed(() => {
    const map = new Map()
    for (const record of data.list.value) map.set(record.peak_assignment_id, record)
    return map
  })

  // iso_child rows (M+1, M+2 ...) grouped by their M0 owner's peak_assignment_id.
  const childrenByOwner = computed(() => {
    const map = new Map()
    for (const record of data.list.value) {
      if (record.role === 'iso_child' && record.owner_peak_assignment_id != null) {
        const siblings = map.get(record.owner_peak_assignment_id) ?? []
        siblings.push(record)
        map.set(record.owner_peak_assignment_id, siblings)
      }
    }
    return map
  })

  // Isotopologue children of an M0 assignment (by its peak_assignment_id).
  const childrenOf = (peakAssignmentId) =>
    peakAssignmentId == null ? [] : (childrenByOwner.value.get(peakAssignmentId) ?? [])

  // The M0 any row belongs to (see familyM0). The anchor a family-scoped
  // judgment is read from and written against - consumed by the verification
  // store and by both surfaces that show a verdict.
  const m0Of = (assignment) => familyM0(assignment, byId.value)

  // The full isotopologue family (M0 + its children), ordered by m/z, for any
  // member of the family. Consumed by the peak inspector.
  const familyOf = (assignment) => {
    const m0 = m0Of(assignment)
    if (!m0) return []
    return [m0, ...childrenOf(m0.peak_assignment_id)].sort(
      (a, b) => a.sample_peak_mz - b.sample_peak_mz
    )
  }

  // Confidence-tier histogram for the run summary. iso_child isotopologues are
  // folded into their M0 and NOT counted, so the tiers count assigned formulas
  // (and unassigned peaks), not every isotopologue peak. Roles reagent/artifact
  // are counted separately (orthogonal to tier).
  const tierCounts = computed(() => {
    const counts = {
      assigned: 0,
      candidate: 0,
      below_assignability: 0,
      unassigned: 0,
      reagent: 0
    }
    for (const record of data.list.value) {
      if (record.role === 'iso_child') continue
      if (record.role === 'reagent' || record.role === 'artifact') {
        counts.reagent += 1
      } else {
        counts[record.tier] = (counts[record.tier] ?? 0) + 1
      }
    }
    return counts
  })

  return {
    ...data,
    run,
    byPeakId,
    forPeak,
    byId,
    childrenOf,
    m0Of,
    familyOf,
    tierCounts,
    detailOf: detail.detailOf,
    loadDetail: detail.loadDetail
  }
})
