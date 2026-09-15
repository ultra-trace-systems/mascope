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
 * The isotope each labelled custom element puts in an ion, spelled the way the
 * isotope generator writes it into the ion's isotopologue formulas.
 *
 * A copy of the registry in `mascope_tools/composition/custom_elements.py`, where
 * `^N` - the 15N of a labelled nitrate reagent - is the only entry today. An
 * element added there has to be added here, or its ions count from their
 * unlabelled remainder again.
 */
const LABELLED_ISOTOPES = new Map([['^N', '[15N]']])

/**
 * One token of a flat formula: a bracketed isotope (`[15N]`), a caret custom
 * element (`^N`) or a plain element (`C`, `Br`), then its count if it has one.
 * The backend's `parse_formula_tokens` pattern, so both read a formula alike.
 */
const FORMULA_TOKEN = /(\[\d+[A-Z][a-z]?\]|\^?[A-Z][a-z]?)(\d*)/g

/**
 * A flat formula's symbols and their counts. A charge sign is not a token.
 *
 * @param {string} formula a formula without parentheses
 * @returns {Object<string, number>} symbol -> count
 */
function formulaTokens(formula) {
  const counts = {}
  for (const [, symbol, count] of formula.matchAll(FORMULA_TOKEN)) {
    counts[symbol] = (counts[symbol] ?? 0) + (count ? Number(count) : 1)
  }
  return counts
}

/**
 * The isotopes an ion carries by design, spelled the way its isotopologue
 * formulas spell them: a labelled reagent's `^N` is `{'[15N]': 1}`, `^N2` is
 * `{'[15N]': 2}`, and an ion without a label carries none. The backend's
 * `labelled_isotopes`.
 *
 * @param {string|null|undefined} ionFormula the ion's formula (`C9H16O7^N-`)
 * @returns {Object<string, number>} labelled isotope -> count
 */
function labelledIsotopes(ionFormula) {
  const labels = {}
  if (typeof ionFormula !== 'string') return labels
  for (const [symbol, count] of Object.entries(formulaTokens(ionFormula))) {
    const isotope = LABELLED_ISOTOPES.get(symbol)
    if (isotope) labels[isotope] = (labels[isotope] ?? 0) + count
  }
  return labels
}

/**
 * The isotopes an isotopologue formula names in brackets, with their counts.
 *
 * @param {string} formula one isotopologue formula
 * @returns {Object<string, number>} bracketed isotope -> count
 */
const substitutedIsotopes = (formula) =>
  Object.fromEntries(
    Object.entries(formulaTokens(formula)).filter(([symbol]) => symbol.startsWith('['))
  )

/**
 * Whether two sets of isotope counts name the same isotopes, each as often.
 *
 * @param {Object<string, number>} a
 * @param {Object<string, number>} b
 * @returns {boolean}
 */
function sameIsotopes(a, b) {
  const isotopes = Object.keys(a)
  return (
    isotopes.length === Object.keys(b).length &&
    isotopes.every((isotope) => a[isotope] === b[isotope])
  )
}

/**
 * Whether an isotopologue formula names the ion's monoisotopic isotopologue.
 *
 * The generator writes a substituted isotope in brackets (`C5[13C]H13O6+`,
 * `[81Br]Br2-`) and the monoisotopic isotopologue - every element at its most
 * abundant isotope - without (`C6H13O6+`, `Br3-`). A labelled reagent's atom is
 * bracketed too, because its isotope is the one the label put there, so the
 * monoisotopic isotopologue of a labelled ion names exactly its labels and
 * nothing else: `[15N]C9H16O7-` for `C9H16O7^N-`. The formula without a bracket
 * is then the reagent's unlabelled remainder, one mass unit below the line the
 * ion is measured by.
 *
 * At a low resolution one line holds several isotopologues, their names joined
 * by "/"; it is the monoisotopic line when any of them is. The same rule as the
 * backend's `is_monoisotopic_formula`.
 *
 * @param {string|null|undefined} formula an isotopologue formula
 * @param {Object<string, number>} labels the ion's labelled isotopes
 *   (`labelledIsotopes`); empty for an ion without a label
 * @returns {boolean}
 */
const isMonoisotopicFormula = (formula, labels) =>
  typeof formula === 'string' &&
  formula.length > 0 &&
  formula.split('/').some((name) => sameIsotopes(substitutedIsotopes(name), labels))

/**
 * The monoisotopic isotopologue of a hit's predicted pattern: the M0 every
 * offset label counts from, the way an isotope table counts - which for a
 * bromine- or chlorine-rich ion is the lightest line of the cluster, not the
 * tallest, and for a labelled ion is the labelled line, not the unlabelled
 * remainder below it.
 *
 * The lightest row stands in when no formula carries the marker that tells the
 * lines apart, and is the same row wherever an element's most abundant isotope
 * is also its lightest. The backend's `monoisotopic_row` resolves it the same way.
 *
 * @param {Array<Object>} children the hit's predicted isotopologues
 * @param {string|null|undefined} ionFormula the ion's formula, which names its
 *   labels; without one, the pattern is read as an unlabelled ion's
 * @returns {Object} the monoisotopic row, or the lightest one
 */
function monoisotopicOf(children, ionFormula) {
  // Copied before sorting: `children` is the hit's own array, and the results
  // table renders from it.
  const ordered = [...children].sort((a, b) => (a.mz ?? 0) - (b.mz ?? 0))
  const labels = labelledIsotopes(ionFormula)
  return (
    ordered.find((row) => isMonoisotopicFormula(row.target_isotope_formula, labels)) ?? ordered[0]
  )
}

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
 * Labels count from the ion's MONOISOTOPIC isotopologue (`monoisotopicOf`), and
 * the label is the nominal mass offset from it. That is the convention the
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
  const children = hit?.children ?? []
  // No predicted pattern to place the peak in: the honest default is the main
  // isotopologue, which is what a single-isotope candidate means anyway.
  if (!children.length) return { label: 'M0', formula: null }

  // The labels are read off the ion formula, which the search spreads onto the
  // hit with the rest of the matched ion; an isotope row carries only its own.
  const main = monoisotopicOf(children, hit?.target_ion_formula)
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

  const offset = Math.round((matched.mz ?? 0) - (main.mz ?? 0))
  return {
    label: offset === 0 ? 'M0' : offset > 0 ? `M+${offset}` : `M${offset}`,
    formula: matched.target_isotope_formula ?? null
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
 * Identity of a hit for per-row UI state (which row is mid-write).
 *
 * Formula AND mechanism: the same composition can be found under two adducts,
 * and the results table's dataKey is the formula alone, so it cannot tell those
 * two rows apart.
 *
 * @param {Object} hit a composition-search result row
 * @returns {string} a key unique to the hit within one result set
 */
export function hitKey(hit) {
  return `${hit?.target_compound_formula}|${hit?.ionization_mechanism_id ?? ''}`
}
