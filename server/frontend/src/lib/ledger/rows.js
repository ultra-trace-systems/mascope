/**
 * What both ledger scopes read off a row the same way: the name of an
 * isotopologue line and the rank a verdict sorts on.
 */
import { formatIsotopeFormula, formatIsotopeLabel } from '@/lib/chem'

/**
 * A line's nominal mass offset from the M0 it folds under ("M+1", "M+2"):
 * the name of a line nothing else names.
 *
 * @param {number|null|undefined} mz the line's m/z
 * @param {number|null|undefined} m0Mz the M0's m/z
 * @returns {string} the offset label, or an empty string when either is unknown
 */
export function offsetLabel(mz, m0Mz) {
  if (mz == null || m0Mz == null) return ''
  const offset = Math.round(mz - m0Mz)
  if (offset > 0) return `M+${offset}`
  return offset < 0 ? `M${offset}` : 'M0'
}

/**
 * A sample-scope line's label: its substitution, counted from the family's
 * M0, wherever the row names one (`isotope_formula`); a source ion's line,
 * which names its heavy isotopes in its label alone, in the same brackets;
 * the offset from the M0 where nothing names it.
 *
 * @param {Object} row the line's assignment row
 * @param {Object|null} m0 the family's M0 row
 * @returns {string}
 */
export function sampleLineLabel(row, m0) {
  if (row.isotope_formula) {
    return formatIsotopeFormula(row.isotope_formula, row.ion_formula ?? m0?.ion_formula)
  }
  return (
    formatIsotopeLabel(row.isotope_label) ||
    offsetLabel(row.sample_peak_mz, m0?.sample_peak_mz) ||
    'iso'
  )
}

/**
 * Where a batch-scope line's substitution is named, and the name: the
 * batch's stamp on the line, the strip that read it as the family's line, or
 * the family's own records of its lines (the M0's predicted isotopologues and
 * the rows it took). Null where none names it.
 *
 * Each record counts only when it is about this family: a stamp or a strip
 * naming another family's peak says nothing about this one, so the pane
 * hands over only the ones it has checked (`stamp`, `strip`).
 *
 * @param {Object} row the line's batch peak
 * @param {Object|null} parent the family's M0 batch peak
 * @param {{ stamp?: Object|null, strip?: Object|null }} records
 * @returns {{ source: 'stamp'|'strip'|'family', label: string, record: Object }|null}
 */
export function batchLineName(row, parent, { stamp = null, strip = null } = {}) {
  if (stamp?.label) return { source: 'stamp', label: stamp.label, record: stamp }
  if (strip?.label) return { source: 'strip', label: strip.label, record: strip }
  const id = row.batch_peak_id
  const predicted = (parent?.diagnostic_isotopologues?.predicted ?? []).find(
    (entry) =>
      entry?.batch_peak_id === id &&
      entry.label &&
      entry.reason !== 'another_parent' &&
      entry.reason !== 'unresolved'
  )
  if (predicted) return { source: 'family', label: predicted.label, record: predicted }
  const took = (parent?.merged_isotopologues?.lines ?? []).find(
    (entry) => entry?.batch_peak_id === id && entry.label
  )
  if (took) return { source: 'family', label: took.label, record: took }
  return null
}

/**
 * A batch-scope line's label: its substitution where a record names it, in
 * the sample scope's brackets ("[81Br]"), else its offset from the M0.
 */
export function batchLineLabel(row, parent, records) {
  const named = batchLineName(row, parent, records)
  if (named) return formatIsotopeLabel(named.label)
  return offsetLabel(row.mz, parent?.mz) || 'iso'
}

// A verdict's place in the verdict column's order: confirmed first, then
// rejected and unsure; a row nobody has judged ranks null, so it sorts last in
// both directions.
const VERDICT_RANK = Object.freeze({ confirmed: 0, rejected: 1, unsure: 2 })

/** The rank the verdict column sorts a row's verdict record on, or null. */
export const verdictRank = (record) => (record ? (VERDICT_RANK[record.verdict] ?? 3) : null)
