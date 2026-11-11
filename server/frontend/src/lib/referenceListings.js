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
 *
 * Both carry at most as many names as a run keeps. How many records name the
 * formula in all comes with the lookup (`known_compounds_total`), and a listing
 * says so where it is more than the names it carries.
 *
 * A list may also say how it reads its compounds, as tags each identity carries
 * (`xrefs.tags`): `background` for a list of what is commonly laboratory-air or
 * instrument background, the cyclic siloxanes first. A tag is the list's
 * reading, shown beside the name; the tier does not weigh it.
 *
 * A row matched to a target compound also carries what the target declares
 * (`reference_listing.target`: its name, its InChIKey, and whether a list the
 * deployment matches against carries the key). That is a candidate too: what
 * the target stands for, not what the peak is. It is read beside the listing,
 * and where a list's identity has the same InChIKey the two are one candidate,
 * shown once with both labels (`targetOf`).
 */

/**
 * The listing an entry carries, the run's own match first.
 *
 * @param {object|null} entry - a detail row, or one of its alternatives
 * @param {Array|null} [matched] - the identities the run matched, where they are
 *   recorded apart from the entry (a row's are in its provenance)
 * @returns {{matched: boolean, identities: Array<object>, total: number}|null}
 */
export function listingOf(entry, matched = entry?.reference_identities) {
  const counted = (identities) =>
    Math.max(
      identities.length,
      Number.isInteger(entry?.known_compounds_total) ? entry.known_compounds_total : 0
    )
  if (Array.isArray(matched) && matched.length) {
    return { matched: true, identities: matched, total: counted(matched) }
  }
  const listed = entry?.known_compounds
  if (Array.isArray(listed) && listed.length) {
    return { matched: false, identities: listed, total: counted(listed) }
  }
  return null
}

/**
 * The listing a ledger row carries, in the shape the helpers below read.
 *
 * A ledger row serves no provenance, so it carries what the inspector reads
 * off `provenance.reference_identities` flattened into one field
 * (`reference_listing`: the first name, its list and its InChIKey, the lists'
 * tags and how many names the run matched); the batch ledger carries the same
 * off its consensus. Always a match the run made, never a lead.
 *
 * @param {object|null} flat - a row's `reference_listing`
 * @returns {{matched: boolean, identities: Array<object>, total: number}|null}
 */
export function ledgerListing(flat) {
  if (!flat || typeof flat !== 'object') return null
  // A row only its target names carries the listing for the target alone.
  if (flat.total === 0) return null
  const tags = Array.isArray(flat.tags) ? flat.tags : []
  return {
    matched: true,
    identities: [
      { name: flat.name, source: flat.source, inchikey: flat.inchikey, xrefs: { tags } }
    ],
    total: Number.isInteger(flat.total) && flat.total > 0 ? flat.total : 1
  }
}

const nameOf = (identity) =>
  typeof identity?.name === 'string' && identity.name ? identity.name : 'Unnamed compound'

/**
 * The first name, and how many more records name the formula.
 *
 * @param {object|null} listing - from `listingOf`
 * @returns {string}
 */
export function listingName(listing) {
  if (!listing) return ''
  const first = nameOf(listing.identities[0])
  const more = (listing.total ?? listing.identities.length) - 1
  return more > 0 ? `${first} +${more}` : first
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

const tagsOf = (identity) =>
  Array.isArray(identity?.xrefs?.tags)
    ? identity.xrefs.tags.filter((tag) => typeof tag === 'string' && tag)
    : []

/** What each tag a list may carry says of the compounds it names. */
const TAG_READINGS = {
  background:
    'commonly a background of laboratory air or of the instrument. It can still be in the sample: whether it is background in these data is for the batch and its blanks to say, and the tier does not weigh it'
}

/**
 * The tags the lists naming the formula carry, each once.
 *
 * @param {object|null} listing - from `listingOf`
 * @returns {Array<string>}
 */
export function listingTags(listing) {
  return [...new Set((listing?.identities ?? []).flatMap(tagsOf))]
}

/**
 * Every name with its list, which kind of listing it is, and what a tag the
 * lists carry says.
 *
 * @param {object|null} listing - from `listingOf`
 * @returns {string}
 */
export function listingTooltip(listing) {
  if (!listing) return ''
  const names = listing.identities.map(
    (identity) => `${nameOf(identity)}${identity?.source ? ` (${identity.source})` : ''}`
  )
  const unlisted = (listing.total ?? names.length) - names.length
  const readings = listingTags(listing).map((tag) => {
    const lists = [
      ...new Set(
        listing.identities
          .filter((identity) => tagsOf(identity).includes(tag) && identity?.source)
          .map((identity) => identity.source)
      )
    ]
    const by = lists.length ? `The ${lists.join(', ')} list` : 'A list'
    return `${by} tags it ${tag}: ${TAG_READINGS[tag] ?? 'a reading of the list'}.`
  })
  return [
    listing.matched
      ? 'The run matched this formula from a reference list.'
      : 'A reference list holds this formula. The run did not match it from the list, so the name is a lead to check.',
    ...names,
    ...(unlisted > 0 ? [`and ${unlisted} more the lists hold for it`] : []),
    ...readings,
    'A formula match names candidate compounds; it is not an identification.'
  ].join('\n')
}

const text = (value) => (typeof value === 'string' && value ? value : null)

/**
 * What the target compound a row was matched to declares, beside its listing.
 *
 * @param {object|null} flat - the row's `reference_listing`, where the read put
 *   the target under `target`
 * @param {object|null} [listing] - the row's listing, from `listingOf` or
 *   `ledgerListing`: an identity of the same InChIKey is the same candidate
 * @returns {{name: ?string, inchikey: ?string, listed: ?boolean,
 *   identity: ?object, unlisted: boolean}|null} - `identity` is the listing's
 *   identity of the target's key, where it has one; `unlisted` is a key no list
 *   carries. Null on a row matched to no target.
 */
export function targetOf(flat, listing = null) {
  const target = flat && typeof flat === 'object' ? flat.target : null
  if (!target || typeof target !== 'object') return null
  const inchikey = text(target.inchikey)
  const identity =
    (inchikey && (listing?.identities ?? []).find((each) => each?.inchikey === inchikey)) || null
  const listed = typeof target.listed === 'boolean' ? target.listed : null
  return {
    name: text(target.name),
    inchikey,
    listed,
    identity,
    unlisted: !identity && listed === false
  }
}

/**
 * The target's name, as its candidate reads.
 *
 * @param {object|null} target - from `targetOf`
 * @returns {string}
 */
export function targetName(target) {
  if (!target) return ''
  return target.name ?? 'Unnamed compound'
}

/**
 * The names the listing carries besides the target's, where the two are one
 * candidate: how many more records name the formula.
 *
 * @param {object|null} target - from `targetOf`
 * @param {object|null} listing - the row's listing
 * @returns {number}
 */
export function targetMore(target, listing) {
  if (!target?.identity || !listing) return 0
  return Math.max((listing.total ?? listing.identities.length) - 1, 0)
}

/**
 * Which the target's candidate is, and what the lists say of it.
 *
 * @param {object|null} target - from `targetOf`
 * @returns {string}
 */
export function targetTooltip(target) {
  if (!target) return ''
  const lines = [
    'The target compound this row was matched to: what the target stands for, not what the peak is.',
    target.inchikey
      ? `${targetName(target)} (InChIKey ${target.inchikey})`
      : `${targetName(target)}. The target declares no structure.`
  ]
  if (target.identity) {
    const by = target.identity.source ? `The ${target.identity.source} list` : 'A reference list'
    lines.push(`${by} names the same structure: one candidate, shown once.`)
  } else if (target.unlisted) {
    lines.push('No reference list carries its InChIKey: a compound the lists do not know.')
  } else if (target.listed) {
    lines.push('A reference list carries its InChIKey.')
  }
  lines.push('A formula match names candidate compounds; it is not an identification.')
  return lines.join('\n')
}

/**
 * The tooltip of a cell that shows both candidates: the target's, then the
 * listing's.
 *
 * @param {object|null} target - from `targetOf`
 * @param {object|null} listing - the row's listing
 * @returns {string}
 */
export function candidatesTooltip(target, listing) {
  return [targetTooltip(target), listingTooltip(listing)].filter(Boolean).join('\n\n')
}
