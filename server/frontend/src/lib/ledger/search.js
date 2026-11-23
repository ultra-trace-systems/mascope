/**
 * The ledger's search box: one query that matches a formula, a listed name or
 * an m/z (docs/dev/assignment_ledger_and_card.md, section 3.4).
 *
 * A query that is a number is an m/z: a row matches when one of its m/z values
 * lies within the scope's window of it, in ppm of the query. Any other
 * query is text, and matches a row whose formula or listed name holds it,
 * ignoring case.
 */

/**
 * The window an imported batch result is matched to batch peaks at
 * (`DEFAULT_MZ_TOLERANCE_PPM` in the backend's `batch_import.py`): the batch
 * scope's m/z search, whose rows come from many runs, and the sample scope's
 * where its run states no fitted width.
 */
export const BATCH_SEARCH_PPM = 5

/**
 * The sample scope's m/z window: the run's own mass gate, the distance from
 * its fitted centre (`mu_ppm`) within which a commit is a plausible member of
 * what the run measured - `cap_z` of its fitted widths (`sigma_ppm`), three
 * where the run recorded no cap (`OFF_CALIBRATION_Z` in the backend's
 * `mass_gate.py`). A run that states no width - an import that disclosed none,
 * a run from before the record - searches at the batch's window.
 *
 * @param {Object|null|undefined} run the run as the run list serves it
 * @returns {{ ppm: number, centrePpm: number }}
 */
export function runSearchWindow(run) {
  const finite = (value) => typeof value === 'number' && Number.isFinite(value)
  const sigma = run?.sigma_ppm
  if (!finite(sigma) || sigma <= 0) return { ppm: BATCH_SEARCH_PPM, centrePpm: 0 }
  const cap = run?.config?.measurement_model?.gate?.cap_z
  return {
    ppm: (finite(cap) && cap > 0 ? cap : 3) * sigma,
    centrePpm: finite(run.mu_ppm) ? run.mu_ppm : 0
  }
}

// A number as a reader types an m/z: digits, at most one decimal point.
const MZ_QUERY = /^(\d+\.?\d*|\.\d+)$/

/**
 * What a query asks for: nothing (an empty query), an m/z, or a text.
 *
 * @param {string|null|undefined} query the search box's value
 * @returns {{ kind: 'none' } | { kind: 'mz', mz: number } | { kind: 'text', text: string }}
 */
export function parseLedgerSearch(query) {
  const trimmed = String(query ?? '').trim()
  if (!trimmed) return { kind: 'none' }
  if (MZ_QUERY.test(trimmed)) {
    const mz = Number(trimmed)
    if (Number.isFinite(mz) && mz > 0) return { kind: 'mz', mz }
  }
  return { kind: 'text', text: trimmed.toLowerCase() }
}

/**
 * Whether a row matches a parsed query.
 *
 * @param {ReturnType<typeof parseLedgerSearch>} parsed the query
 * @param {Object} reading what the row offers the search
 * @param {Array<string|null>} reading.texts its formula and listed names
 * @param {Array<number|null>} reading.mzs its m/z, and its isotope lines'
 * @param {number|{ ppm: number, centrePpm?: number }} window the m/z window:
 *   its half-width in ppm of the query, and where a measured m/z is expected to
 *   sit from it, in ppm (the run's fitted centre; 0 by default)
 * @returns {boolean}
 */
export function matchesLedgerSearch(parsed, { texts = [], mzs = [] }, window) {
  if (parsed.kind === 'none') return true
  if (parsed.kind === 'mz') {
    const { ppm, centrePpm = 0 } = typeof window === 'number' ? { ppm: window } : window
    return mzs.some(
      (mz) =>
        typeof mz === 'number' && Math.abs(((mz - parsed.mz) / parsed.mz) * 1e6 - centrePpm) <= ppm
    )
  }
  return texts.some((text) => typeof text === 'string' && text.toLowerCase().includes(parsed.text))
}
