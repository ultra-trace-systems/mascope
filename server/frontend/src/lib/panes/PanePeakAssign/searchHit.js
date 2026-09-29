import { formatIsotopeFormula, monoisotopicIsotope } from '@/lib/chem'

/**
 * What a composition-search hit means when it is committed to a peak.
 *
 * The re-search results table and the assignment ledger speak different
 * languages: a hit is an ION scored against the spectrum, carrying its match
 * fields at the top level and its search parameters under `cheminfo`, while a
 * ledger row is one PEAK with one composition on it. This module is the
 * translation, kept out of the pane so the rules in it can be tested without
 * mounting a DataTable.
 */

/**
 * Which isotopologue of a search hit's ion the searched peak actually is.
 *
 * The composition search scores a whole ION against the spectrum and reports one
 * row per candidate compound, but the peak in hand may be any isotope of that
 * ion - a heavy-isotope isotopologue lands in the results just as readily as the
 * main peak does. Committing every hit as an M0 would therefore enter a
 * compound's isotopologue into the ledger as the compound's main peak, which
 * everything that folds an isotopologue family onto its M0 (the tier histogram,
 * the batch consensus, a verification verdict) would then believe.
 *
 * Labels count from the ion's MONOISOTOPIC isotopologue (`monoisotopicIsotope`),
 * and the label is the nominal mass offset from it. That is the convention the
 * assignment engine's `monoisotopic_row` and `_isotope_offset_label` use, so a
 * hand-assigned row reads like an engine-assigned one: for a bromine-rich ion
 * the lightest peak of the cluster is the M0 and the tallest is its M+2, as in
 * an isotope table, and for a 15N-labelled ion the labelled line is the M0 and
 * the reagent's unlabelled remainder below it is the M-1.
 *
 * @param {Object} hit a composition-search result row
 * @returns {{label: string, formula: string|null}} the isotopologue label
 *   ('M0', 'M+1', 'M-1' ...) and its full isotopologue formula when the hit
 *   carries one
 */
export function isotopeOfHit(hit) {
  const placed = placeInPattern(hit)
  // No predicted pattern to place the peak in: the honest default is the main
  // isotopologue, which is what a single-isotope candidate means anyway.
  if (!placed) return { label: 'M0', formula: null }

  const { main, matched } = placed
  const offset = Math.round((matched.mz ?? 0) - (main.mz ?? 0))
  return {
    label: offset === 0 ? 'M0' : offset > 0 ? `M+${offset}` : `M${offset}`,
    formula: matched.target_isotope_formula ?? null
  }
}

/**
 * The hit's monoisotopic row and the row the search matched at the peak.
 *
 * @param {Object} hit a composition-search result row
 * @returns {{children: Array<Object>, main: Object, matched: Object}|null} null
 *   when the hit carries no predicted pattern
 */
function placeInPattern(hit) {
  const children = hit?.children ?? []
  if (!children.length) return null

  // The labels are read off the ion formula, which the search spreads onto the
  // hit with the rest of the matched ion; an isotope row carries only its own.
  const main = monoisotopicIsotope(children, hit?.target_ion_formula)
  // The isotope the search matched at this peak. Taken from the hit's own
  // `cheminfo` rather than from the focused peak, so the answer does not depend
  // on which peak happens to be focused when the button is clicked.
  const searched = hit?.cheminfo?.target_isotope_mz
  const matched =
    searched == null
      ? main
      : children.reduce(
          (best, row) =>
            Math.abs((row.mz ?? 0) - searched) < Math.abs((best.mz ?? 0) - searched) ? row : best,
          children[0]
        )
  return { children, main, matched }
}

/**
 * The line of its ion a search hit was read at, when that is not the ion's
 * monoisotopic line.
 *
 * A search that reads a peak as any line of a candidate's ion finds a compound
 * whose 13C line, whose dibromide's brightest line, or whose labelled reagent's
 * unlabelled remainder the peak is. The results table has to say which line,
 * or the row reads as the compound's monoisotopic mass on a peak it is not.
 *
 * Named as the peak inspector names an isotopologue row, by the isotopes it
 * substitutes (`[13C]`, `[81Br]`, `[14N]`), with the offset the hand button
 * commits it under (`isotopeOfHit`) - so the tag says what a click would write.
 *
 * @param {Object} hit a composition-search result row
 * @returns {{name: string, offset: string, share: number|null}|null} the line's
 *   name, its offset label, and its share of the ion's brightest line when the
 *   pattern gives abundances; null for a hit read at its monoisotopic line
 */
export function readLineOfHit(hit) {
  const { label } = isotopeOfHit(hit)
  if (label === 'M0') return null
  const { children, matched } = placeInPattern(hit)
  const brightest = Math.max(...children.map((row) => row.relative_abundance ?? 0))
  const share =
    brightest > 0 && matched.relative_abundance != null
      ? matched.relative_abundance / brightest
      : null
  return {
    name: formatIsotopeFormula(matched.target_isotope_formula, hit?.target_ion_formula) || label,
    offset: label,
    share
  }
}

/**
 * Whether a hit is a complete enough assignment to be committed at all.
 *
 * A formula without its adduct is half an assignment: the endpoint requires the
 * ionization mechanism because a verification's identity is
 * (sample_peak_id, assigned_formula, ionization_mechanism_id), so a formula
 * committed under no mechanism could never carry a verdict. `set_assignment`
 * refuses one with a 422 the user would meet as a bare toast, so the control is
 * withheld instead of offering a write that cannot succeed.
 *
 * Every hit this search produces does carry a mechanism - the backend pairs a
 * composition result with the matched ion of the same mechanism and drops it
 * otherwise - so this guards the control against a hit shape the search does
 * not currently emit rather than one it does.
 *
 * @param {Object} hit a composition-search result row
 * @returns {boolean} true when the hit names both a composition and its adduct
 */
export function canCurateHit(hit) {
  return Boolean(hit?.target_compound_formula && hit?.ionization_mechanism_id)
}

/**
 * The `set_assignment` request body for committing a hit to a peak.
 *
 * Sends only what the hit actually measured. `plausibility` is deliberately
 * absent even though the results table shows one: it is a pure function of the
 * formula, so the server computes it from what it commits rather than take a
 * number about chemistry from the client. The scores that ARE sent came from
 * this server's own search moments earlier; it re-tiers them under the run's
 * bands and records where they came from.
 *
 * Callers gate on `canCurateHit` first, so the mechanism is always there; it is
 * still sent as an explicit null when it is not, because a dropped key reads as
 * "field required" while a null names the field the hit was missing.
 *
 * @param {Object} hit a composition-search result row
 * @returns {Object} the PATCH body for `peak.curate()`
 */
export function curationBodyForHit(hit) {
  const isotope = isotopeOfHit(hit)
  return {
    action: 'set_assignment',
    assigned_formula: hit?.target_compound_formula,
    ionization_mechanism_id: hit?.ionization_mechanism_id ?? null,
    ion_formula: hit?.target_ion_formula ?? null,
    isotope_label: isotope.label,
    isotope_formula: isotope.formula,
    fit_score: hit?.fit_score ?? null,
    mz_error_ppm: hit?.cheminfo?.target_isotope_mz_error_ppm ?? null
  }
}

/**
 * Identity of a hit within one result set: the results table's row key, and
 * per-row UI state (which row is mid-write, which is expanded).
 *
 * Formula AND mechanism: the same composition can be found under two adducts -
 * at its monoisotopic line under one and at an isotopologue line under the
 * other - and a key of the formula alone makes those two rows one.
 *
 * @param {Object} hit a composition-search result row
 * @returns {string} a key unique to the hit within one result set
 */
export function hitKey(hit) {
  return `${hit?.target_compound_formula}|${hit?.ionization_mechanism_id ?? ''}`
}
