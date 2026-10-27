import { docUrl } from '@/stores/ui/help'

/**
 * The reference lists a deployment has loaded, as the Reference lists dialog
 * (under Settings) shows them.
 *
 * `GET /reference/sources` answers one row per active source, which the
 * `reference` data store holds; `GET /reference/sources/<name>/compounds`
 * pages through what one version of a source holds, which the dialog reads a
 * page at a time when a row is opened.
 */

/** Species per page in a source's expander. */
export const SPECIES_PAGE_SIZE = 25

/** Publications a list's row shows before a button opens the rest. */
export const CITATIONS_SHOWN = 2

/** The user guide on loading a list, in the docs the app serves, which the empty state links to. */
export const REFERENCE_LISTS_GUIDE_URL = docUrl('guides/reference-lists/')

const POLARITIES = { positive: 'Positive', negative: 'Negative' }

/**
 * The polarity a source's compounds are matched in. A source row records none
 * for both, which a list that says `both` and a CSV load as.
 *
 * @param {string|null|undefined} polarity
 * @returns {string}
 */
export function polarityLabel(polarity) {
  return POLARITIES[polarity] ?? 'Both'
}

/** Carbon and hydrogen first, the rest alphabetically, as a formula reads. */
const hillSorted = (elements) => {
  const head = ['C', 'H'].filter((symbol) => elements.includes(symbol))
  return [...head, ...elements.filter((symbol) => !head.includes(symbol)).sort()]
}

/**
 * The window a source's formulas are matched in, in one line, as
 * `KnownWindow.describe` writes it on the server: `C, H, N, O, S; C <= 40;
 * <= 700 Da`, or `Unbounded`.
 *
 * @param {{elements?: string[]|null, max_carbon?: number|null, max_mass?: number|null}|null|undefined} window
 * @returns {string}
 */
export function windowLabel(window) {
  const elements = window?.elements ?? null
  const maxCarbon = window?.max_carbon ?? null
  const maxMass = window?.max_mass ?? null
  if (elements === null && maxCarbon === null && maxMass === null) return 'Unbounded'
  return [
    elements === null ? 'any element' : hillSorted(elements).join(', '),
    maxCarbon === null ? 'any carbon count' : `C <= ${maxCarbon}`,
    maxMass === null ? 'any mass' : `<= ${maxMass} Da`
  ].join('; ')
}

/**
 * A timestamp in the browser's locale; a dash for none.
 *
 * @param {string|null|undefined} stamp
 * @returns {string}
 */
export function loadedLabel(stamp) {
  if (!stamp) return '-'
  const date = new Date(stamp)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString()
}

/**
 * Where a species' or a list's reference leads: doi.org for a DOI, nothing
 * for anything else, which is shown as written.
 *
 * @param {string|null|undefined} reference
 * @returns {string|null}
 */
export function referenceUrl(reference) {
  const doi = reference?.trim().replace(/^(https?:\/\/(dx\.)?doi\.org\/|doi:\s*)/i, '')
  return doi && /^10\.\d{4,}\/\S+$/.test(doi) ? `https://doi.org/${doi}` : null
}
