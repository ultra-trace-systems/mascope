// The confidence tiers a peak assignment can land in, declared once.
//
// The same four values were four facts kept in step by hand: the chip's label
// and severity (BaseTierTag), the rank the tier column sorts by (the sample
// ledger), the option list a tier filter offers, and the buckets a tier
// histogram counts into. Each copy could drift from the others, and one did -
// the batch-peaks table sorted the raw tier string, which ordered the tiers
// alphabetically and read as nonsense next to the sample ledger's ranked
// column.
//
// Order here is meaning, not presentation: the list is written most confident
// (assigned) to least (unassigned), and a tier's rank is its position in it.
// Sorting a tier column ascending therefore reads as "best first", which is
// what alphabetical order got wrong.

// The tier a row falls back to when it carries none, or one this list does not
// know. An assignment always has an outcome, so an unreadable tier is the
// absence of one rather than a fifth kind - counting it as its own bucket is
// how a histogram stops summing to the number of rows.
export const FALLBACK_TIER = 'unassigned'

const TIER_DEFINITIONS = [
  {
    key: 'assigned',
    label: 'assigned',
    severity: 'success',
    icon: 'ph ph-seal-check'
  },
  {
    key: 'candidate',
    label: 'candidate',
    severity: 'warn',
    icon: 'ph ph-circle-half'
  },
  {
    // Labelled short because it sits in a chip beside a percentage; the full
    // value is what the tooltip and the filter menu say.
    key: 'below_assignability',
    label: 'below',
    severity: 'secondary',
    icon: 'ph ph-minus-circle'
  },
  {
    key: FALLBACK_TIER,
    label: 'unassigned',
    severity: 'secondary',
    icon: 'ph ph-circle-dashed'
  }
]

/** Every tier, in confidence order. */
export const TIERS = TIER_DEFINITIONS.map(({ key }) => key)

/** Tier -> sort rank; ascending rank is descending confidence. */
export const TIER_RANK = Object.fromEntries(TIER_DEFINITIONS.map(({ key }, index) => [key, index]))

/** Tier -> chip label, PrimeVue Tag severity, phosphor icon. */
export const TIER_META = Object.fromEntries(TIER_DEFINITIONS.map((tier) => [tier.key, tier]))

/**
 * Whether a value is one of the tiers.
 *
 * Membership is tested against the list, not with `in` against the rank map:
 * `in` walks the prototype chain, so a record whose tier read "constructor"
 * would be accepted as a tier and ranked by a function.
 *
 * @param {string} tier the tier as stored on the record
 * @returns {boolean} true when this build knows the tier
 */
export const isTier = (tier) => TIERS.includes(tier)

/**
 * The tier a row belongs to, with anything unrecognized folded into
 * `FALLBACK_TIER`.
 *
 * @param {string} tier the tier as stored on the record
 * @returns {string} one of `TIERS`
 */
export const tierBucket = (tier) => (isTier(tier) ? tier : FALLBACK_TIER)

/**
 * Sort rank for a tier, so an unknown one sorts last rather than first.
 *
 * @param {string} tier the tier as stored on the record
 * @returns {number} its index in `TIERS`
 */
export const tierRank = (tier) => TIER_RANK[tierBucket(tier)]

/**
 * Chip presentation for a tier.
 *
 * @param {string} tier the tier as stored on the record
 * @returns {{key: string, label: string, severity: string, icon: string}}
 */
export const tierMeta = (tier) => TIER_META[tierBucket(tier)]

/**
 * Histogram of tiers over `records`, one bucket per tier and always all of
 * them.
 *
 * Every record lands in exactly one bucket, so the counts sum to
 * `records.length` and a strip built from them can be read as a breakdown of
 * the table below it. Zero-count tiers are kept: a chip that vanishes when its
 * count reaches zero moves the ones beside it, and "none of these" is an answer
 * worth showing.
 *
 * @param {Array<Object>} records the rows to count
 * @param {Function} of reads the tier off one record
 * @returns {Object<string, number>} counts keyed by tier, in confidence order
 */
export function countTiers(records, of = (record) => record?.tier) {
  const counts = Object.fromEntries(TIERS.map((tier) => [tier, 0]))
  for (const record of records ?? []) {
    counts[tierBucket(of(record))] += 1
  }
  return counts
}
