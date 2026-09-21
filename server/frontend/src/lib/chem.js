import { norm } from '@/lib/utils'
import { fromSpreadsheet } from '@/lib/table'

/**
 * A neutral formula's identity, however it is written: its element counts in a
 * fixed order, so `C1H4N2O1` and `CH4N2O` are one neutral. A labelled atom
 * (`[15N]`, `^N`) counts as its own symbol. A formula that does not read as
 * one comes back as it was given, so it is never merged with another.
 *
 * @param {string|null|undefined} formula - a neutral formula
 * @returns {string} the identity; empty for no formula
 */
export function neutralKey(formula) {
  const text = typeof formula === 'string' ? formula.trim() : ''
  if (!text) return ''
  const token = /^(\[\d+[A-Z][a-z]?\]|\^?[A-Z][a-z]?)(\d*)/
  const counts = new Map()
  let rest = text
  while (rest) {
    const match = token.exec(rest)
    if (!match) return text
    const [whole, symbol, count] = match
    counts.set(symbol, (counts.get(symbol) ?? 0) + (count ? Number(count) : 1))
    rest = rest.slice(whole.length)
  }
  return [...counts.entries()]
    .filter(([, n]) => n > 0)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([symbol, n]) => `${symbol}${n}`)
    .join(' ')
}

/**
 * Validates chemical formula format
 * Supports standard chemical notation including parentheses and empty parentheses ()
 *
 * @param {string} formula - Chemical formula to validate
 * @returns {boolean} True if valid chemical formula
 *
 * Examples:
 * - "C6H12O6" ✓
 * - "Ca(OH)2" ✓
 * - "(NH4)2SO4" ✓
 * - "()" ✓ (empty parentheses allowed)
 * - "H^NO3" ✓ (custom element denoted by caret)
 * - "invalid123" ✗
 */
export function isValidChemicalFormula(formula) {
  if (!formula) return false
  const normalized = norm(formula)

  // Allow empty parentheses () as valid
  if (normalized === '()') return true

  // Chemical formula regex, allows caret (^) prefix for custom elements (e.g. ^N) and parenthesis groups as part of the formula
  const regex = /^(?:\^?[A-Z][a-z]?\d*|\([^()]+\)\d*)+$/
  return regex.test(normalized)
  // See:
  //   Debugger: https://regex101.com/r/Mbjq8C/1
  //   Inspiration: https://stackoverflow.com/questions/23602175/regex-for-parsing-chemical-formulas#23602425
}

/**
 * Checks if two compounds are the same using matching by CAS number OR formula
 *
 * @param {Object} compound1 - First compound to compare
 * @param {Object} compound2 - Second compound to compare
 * @returns {boolean} True if compounds match by CAS OR formula
 */
export function isSameCompound(compound1, compound2) {
  if (!compound1?.target_compound_formula?.trim() || !compound2?.target_compound_formula?.trim()) {
    return false
  }

  const casMatch =
    compound1.cas_number &&
    compound2.cas_number &&
    norm(compound1.cas_number) === norm(compound2.cas_number)

  const formulaMatch =
    norm(compound1.target_compound_formula, true) === norm(compound2.target_compound_formula, true)

  return casMatch || formulaMatch
}

/**
 * Finds existing compound in database using backend matching logic
 * Matches by CAS number OR formula
 *
 * @param {Array} compoundList - List of existing compounds to search in
 * @param {Object} searchCompound - Compound to search for with properties:
 *   - target_compound_formula: Chemical formula
 *   - cas_number: CAS registry number
 * @returns {Object|null} Existing compound if found, null otherwise
 */
export function findExistingCompound(compoundList, searchCompound) {
  if (!searchCompound?.target_compound_formula?.trim()) return null

  return compoundList.find((comp) => isSameCompound(comp, searchCompound))
}

/**
 * Parses spreadsheet cells pasted as target compounds.
 *
 * The column layout is inferred from the number of pasted columns:
 * - 1 column:  formula
 * - 2 columns: name, formula
 * - 3 columns: name, formula, CAS number
 *
 * A single-column paste may start with a header cell (e.g. "Formula"); it is
 * dropped when it is not a valid formula but the row below it is.
 *
 * @param {string} text - Raw clipboard text (tab-separated cells)
 * @returns {Array<Object>} Parsed compound rows
 */
export function parseCompoundPaste(text) {
  const lines = text.split('\n').filter((line) => line.trim().length)
  const cols = lines[0] ? lines[0].split('\t').length : 0
  const fields =
    cols === 1
      ? ['target_compound_formula']
      : ['target_compound_name', 'target_compound_formula', 'cas_number']
  const { rows } = fromSpreadsheet(text, fields)
  if (
    cols === 1 &&
    rows.length > 1 &&
    !isValidChemicalFormula(rows[0].target_compound_formula) &&
    isValidChemicalFormula(rows[1].target_compound_formula)
  ) {
    rows.shift()
  }
  return rows
}

/**
 * Validates compound rows parsed from a spreadsheet paste.
 *
 * Every row needs a formula. Single-column pastes are additionally checked
 * against isValidChemicalFormula, so that pasting a name-only column is
 * rejected instead of imported as bogus formulas.
 *
 * @param {Array<Object>} data - Rows from parseCompoundPaste
 * @returns {{valid: boolean, severity: string, message: string}}
 */
export function validateCompoundPaste(data) {
  if (!data || !Array.isArray(data) || data.length === 0 || !data[0]) {
    return { valid: false, severity: 'error', message: 'No valid data found in paste' }
  }
  const cols = Object.keys(data[0]).length
  if (cols > 3) {
    return {
      valid: false,
      severity: 'warn',
      message: `You pasted ${cols} columns but 1 to 3 are expected`
    }
  }
  if (data.some((row) => !row?.target_compound_formula?.length)) {
    return {
      valid: false,
      severity: 'warn',
      message: 'Some rows are missing a formula, which is required'
    }
  }
  if (cols === 1) {
    const invalid = data.find((row) => !isValidChemicalFormula(row.target_compound_formula))
    if (invalid) {
      return {
        valid: false,
        severity: 'warn',
        message: `'${invalid.target_compound_formula}' is not a valid chemical formula`
      }
    }
  }
  return {
    valid: true,
    severity: 'success',
    message: `Pasted ${data.length} compound${data.length === 1 ? '' : 's'}`
  }
}

/**
 * The isotopes each labelled custom element puts in an ion, spelled the way the
 * isotope generator writes them into the ion's isotopologue formulas. The
 * labelled atom is bracketed (`[15N]`); the atom of the reagent's unlabelled
 * remainder is written as the plain element, which is its lightest isotope, and
 * is named `[14N]` here for when a label has to say so.
 *
 * The frontend's one copy of the registry in
 * `mascope_tools/composition/custom_elements.py`, where `^N` - the 15N of a
 * labelled nitrate reagent - is the only entry today. An element added there has
 * to be added here, or its ions are read as unlabelled: the isotopologue tables
 * label the remainder M0, and a search hit committed from the peak inspector is
 * counted from it.
 */
const LABELLED_ELEMENTS = new Map([['^N', { labelled: '[15N]', unlabelled: '[14N]' }]])

/** The isotope a labelled atom has in the unlabelled remainder: `[15N]` -> `[14N]`. */
const UNLABELLED_ISOTOPES = new Map(
  [...LABELLED_ELEMENTS.values()].map(({ labelled, unlabelled }) => [labelled, unlabelled])
)

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
 * @returns {Object<string, number>} symbol -> count, in order of first appearance
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
export function labelledIsotopes(ionFormula) {
  const labels = {}
  if (typeof ionFormula !== 'string') return labels
  for (const [symbol, count] of Object.entries(formulaTokens(ionFormula))) {
    const element = LABELLED_ELEMENTS.get(symbol)
    if (element) labels[element.labelled] = (labels[element.labelled] ?? 0) + count
  }
  return labels
}

/**
 * The isotopes an isotopologue formula names, with their counts: its bracketed
 * isotopes, and each labelled custom element as the isotope it stands for.
 *
 * The generator brackets a labelled atom like any substituted isotope
 * (`[15N]C9H16O7-`), but an imported run can write a line in the ion's own
 * notation, caret and all - the M0 of `C10H18O7^N-` as `C10H18O7^N-`. Both spell
 * the same composition, so `^N` counts as `[15N]`, added to any `[15N]` beside
 * it: in a mixed spelling that one is an atom of the analyte's own at 15N.
 *
 * @param {string} formula one isotopologue formula
 * @returns {Object<string, number>} isotope, spelled in brackets -> count
 */
function substitutedIsotopes(formula) {
  const isotopes = {}
  for (const [symbol, count] of Object.entries(formulaTokens(formula))) {
    const isotope = symbol.startsWith('[') ? symbol : LABELLED_ELEMENTS.get(symbol)?.labelled
    if (isotope) isotopes[isotope] = (isotopes[isotope] ?? 0) + count
  }
  return isotopes
}

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
 * ion is measured by. An imported run may spell that M0 with the ion's own caret
 * element instead (`C10H18O7^N-` for `C10H18O7^N-`), which names the same labels
 * (`substitutedIsotopes`).
 *
 * At a low resolution one line holds several isotopologues, their names joined
 * by "/"; it is the monoisotopic line when any of them is. The rule of the
 * backend's `is_monoisotopic_formula`, which meets only the generator's
 * bracketed spelling.
 *
 * @param {string|null|undefined} formula an isotopologue formula
 * @param {Object<string, number>} [labels] the ion's labelled isotopes
 *   (`labelledIsotopes`); none for an ion without a label
 * @returns {boolean}
 */
export const isMonoisotopicFormula = (formula, labels = {}) =>
  typeof formula === 'string' &&
  formula.length > 0 &&
  formula.split('/').some((name) => sameIsotopes(substitutedIsotopes(name), labels))

/**
 * A key that sorts bracketed isotopes into the Hill order the generator writes
 * them in (`[13C][15N]C8H16O7-`): carbon first, then hydrogen, then the rest
 * alphabetically. Without carbon, Hill sorts hydrogen alphabetically as well,
 * which still puts it before nitrogen - the only element this places today.
 *
 * @param {string} isotope a bracketed isotope (`[14N]`)
 * @returns {string}
 */
function hillKey(isotope) {
  const element = isotope.match(/[A-Z][a-z]?/)[0]
  return element === 'C' ? '0' : element === 'H' ? '1' : `2${element}`
}

/**
 * How one isotopologue differs from its ion's monoisotopic isotopologue, in
 * bracketed isotopes: each isotope it carries beyond the ion's labels (`[13C]`),
 * and each labelled atom it carries at its unlabelled isotope instead (`[14N]`),
 * in the order the generator would write them. Empty for the monoisotopic
 * isotopologue itself.
 *
 * @param {string} name one isotopologue formula, not a "/"-joined line
 * @param {Object<string, number>} labels the ion's labelled isotopes
 * @returns {string}
 */
function substitutionOf(name, labels) {
  const substituted = substitutedIsotopes(name)
  const tokens = Object.entries(substituted)
    .map(([isotope, count]) => [isotope, count - (labels[isotope] ?? 0)])
    .filter(([, count]) => count > 0)
  for (const [label, count] of Object.entries(labels)) {
    const missing = count - (substituted[label] ?? 0)
    if (missing <= 0) continue
    const isotope = UNLABELLED_ISOTOPES.get(label)
    const after = tokens.findIndex(([other]) => hillKey(other) > hillKey(isotope))
    tokens.splice(after === -1 ? tokens.length : after, 0, [isotope, missing])
  }
  return tokens.map(([isotope, count]) => (count > 1 ? `${isotope}${count}` : isotope)).join('')
}

/**
 * The compact label of an isotopologue in its ion's pattern: "M0" for the
 * monoisotopic isotopologue, and otherwise how it differs from that one, in
 * bracketed isotopes with their counts.
 *
 * The difference is counted from the M0 rather than read off the brackets,
 * because a labelled ion's M0 is bracketed itself (`isMonoisotopicFormula`).
 * For the 15N-nitrate ion `C9H16O7^N-` the M0 is `[15N]C9H16O7-`, its 13C line
 * `[13C][15N]C8H16O7-` reads "[13C]", and the reagent's unlabelled remainder
 * `C9H16NO7-`, one mass unit below the M0, reads "[14N]": its labelled atom is
 * at 14N. An ion without a label has nothing to subtract, so its labels are its
 * brackets, and the formula without one is its M0.
 *
 * A low-resolution line that merges several isotopologues is "M0" when any of
 * them is, and otherwise each one's label joined by "/".
 *
 * @param {string|null|undefined} formula an isotopologue formula, or a line's
 *   "/"-joined formulas
 * @param {string|null|undefined} [ionFormula] the ion's formula, which names its
 *   labels (`C9H16O7^N-`); without one, the formula is read as an unlabelled ion's
 * @returns {string} the label, or an empty string for no formula
 *
 * Examples:
 * - "C3H6O3" -> "M0" (no isotopes)
 * - "[13C]C2H6O3" -> "[13C]"
 * - "[13C]C2[2H]H5O3" -> "[13C][2H]"
 * - "[13C]2CH6O3" -> "[13C]2"
 * - "[13C]C2H6O3/C3H6[18O]O2" -> "[13C]/[18O]"
 * - "[15N]C9H16O7-" of "C9H16O7^N-" -> "M0"
 * - "C9H16NO7-" of "C9H16O7^N-" -> "[14N]"
 * - "[15N]HNO6-" of "HO6^N2-" -> "[14N]"
 * - "C10H18O7^N-" of "C10H18O7^N-" -> "M0" (the label in caret spelling)
 * - "[13C]C9H18O7^N-" of "C10H18O7^N-" -> "[13C]"
 */
export function formatIsotopeFormula(formula, ionFormula) {
  if (!formula) return ''
  const labels = labelledIsotopes(ionFormula)
  if (isMonoisotopicFormula(formula, labels)) return 'M0'
  return formula
    .split('/')
    .map((name) => substitutionOf(name, labels))
    .join('/')
}
