// Which ledger rows are lines of another row's isotope pattern.
//
// The sample ledger folds such a row under the row it belongs to, the inspector
// lists it in that row's isotopologue table, and a verdict on it is read off
// that row (the ledger store's familyM0). Kept apart from the store so a pane
// can ask without loading it.

/**
 * Whether a row is a line of another row's isotope pattern, folded under it.
 *
 * Two kinds of row are. An analyte's isotopologue is an `iso_child`. A source
 * ion's isotope lines keep the `reagent` role - they are the source's peaks, not
 * the sample's - and the reagent pass names the ion's monoisotopic row as their
 * owner, so it is the owner that makes one a line. A reagent row with no owner
 * is an ion of its own: its monoisotopic line, or a line from a run written
 * before the pass linked them.
 *
 * @param {Object} record a ledger row, or null
 * @returns {boolean} whether the row belongs under another row
 */
export function isIsotopeLine(record) {
  if (!record) return false
  if (record.role === 'iso_child') return true
  return record.role === 'reagent' && record.owner_peak_assignment_id != null
}
