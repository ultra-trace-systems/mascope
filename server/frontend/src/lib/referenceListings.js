/**
 * What a reference list calls a formula, as an assignment row carries it.
 *
 * Two sources, which say different things. A run records the list identities
 * of a formula it MATCHED from a reference list: `provenance.reference_identities`
 * on a row, `reference_identities` on a runner-up. The detail read adds the
 * compounds any list holds for a formula (`known_compounds`), including a
 * formula the run reached through the formula search - a lead to check rather
 * than a match the run made. Either way a formula match names candidate
 * compounds, not an identification.
 */

/**
 * The listing an entry carries, the run's own match first.
 *
 * @param {object|null} entry - a detail row, or one of its alternatives
 * @param {Array|null} [matched] - the identities the run matched, where they are
 *   recorded apart from the entry (a row's are in its provenance)
 * @returns {{matched: boolean, identities: Array<object>}|null}
 */
export function listingOf(entry, matched = entry?.reference_identities) {
  if (Array.isArray(matched) && matched.length) return { matched: true, identities: matched }
  const listed = entry?.known_compounds
  if (Array.isArray(listed) && listed.length) return { matched: false, identities: listed }
  return null
}

const nameOf = (identity) =>
  typeof identity?.name === 'string' && identity.name ? identity.name : 'Unnamed compound'

/**
 * The first name, and how many more compounds share the formula.
 *
 * @param {object|null} listing - from `listingOf`
 * @returns {string}
 */
export function listingName(listing) {
  if (!listing) return ''
  const [first, ...rest] = listing.identities
  return rest.length ? `${nameOf(first)} +${rest.length}` : nameOf(first)
}

/**
 * The list the first name comes from.
 *
 * @param {object|null} listing - from `listingOf`
 * @returns {string}
 */
export function listingSource(listing) {
  const source = listing?.identities?.[0]?.source
  return typeof source === 'string' ? source : ''
}

/**
 * Every name with its list, and which kind of listing it is.
 *
 * @param {object|null} listing - from `listingOf`
 * @returns {string}
 */
export function listingTooltip(listing) {
  if (!listing) return ''
  const names = listing.identities.map(
    (identity) => `${nameOf(identity)}${identity?.source ? ` (${identity.source})` : ''}`
  )
  return [
    listing.matched
      ? 'The run matched this formula from a reference list.'
      : 'A reference list holds this formula. The run did not match it from the list, so the name is a lead to check.',
    ...names,
    'A formula match names candidate compounds; it is not an identification.'
  ].join('\n')
}
