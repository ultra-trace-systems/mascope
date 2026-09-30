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

// `description` is the tier's meaning in one line, as its chip says it on hover.
const TIER_DEFINITIONS = [
  {
    key: 'assigned',
    label: 'assigned',
    description: 'Assigned: strong evidence for this formula',
    severity: 'success',
    icon: 'ph ph-seal-check'
  },
  {
    key: 'candidate',
    label: 'candidate',
    description: 'Candidate: a plausible formula with weaker support',
    severity: 'warn',
    icon: 'ph ph-circle-half'
  },
  {
    // Labelled short to keep the chip small; the full name is what the
    // tooltip and the filter menu say.
    key: 'below_assignability',
    label: 'below',
    description: 'Below assignability: a formula was found, but the evidence is too weak to trust',
    severity: 'secondary',
    icon: 'ph ph-minus-circle'
  },
  {
    key: FALLBACK_TIER,
    label: 'unassigned',
    description: 'Unassigned: no composition explained the peak',
    severity: 'secondary',
    icon: 'ph ph-circle-dashed'
  }
]

/**
 * What the tier column and the inspector's tier row say of the tiering itself
 * while its rules are being built: under the assignment quality plan
 * (docs/dev/assignment_quality_plan.md, stage 3) a week's work can move a third
 * of a set's tiers, and a tier shown with the same face as the m/z beside it
 * would read as settled. A fixed string rather than a setting, set to null when
 * the plan's stage 3 gate passes; every surface that shows the marker reads it
 * from here and shows nothing once it is null.
 */
export const TIERING_PROVISIONAL = Object.freeze({
  label: 'provisional',
  tooltip:
    'The tiering is provisional: its rules are still being developed, and a ' +
    "row's tier can change between engine versions. Read a tier as the engine's " +
    'current reading of the evidence, not as a settled verdict.'
})

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
 * The roles that account for a peak without a formula, each a bucket of its
 * own after the tiers: a reagent peak is the source's own ion, an artifact the
 * instrument's ringing. Their rows sit at tier `unassigned`, which is true - no
 * compound was assigned - so without buckets of their own they were filtered,
 * counted and sorted among the peaks nothing explained. Shared by the sample
 * ledger, whose rows carry a `role`, and the batch ledger, whose anchors carry
 * a `consensus_role`, in the order the strips show them.
 */
export const ROLE_BUCKETS = ['reagent', 'artifact']

/**
 * The bucket a row is filtered and counted in: its role where one accounts
 * for it, else its tier.
 *
 * @param {string} tier the tier as stored on the record
 * @param {string|null} role the role, or null
 * @returns {string} one of `TIERS` or `ROLE_BUCKETS`
 */
export const bucketOf = (tier, role) => (ROLE_BUCKETS.includes(role) ? role : tierBucket(tier))

/**
 * Sort rank for a row: the tiers in confidence order, then the roles in the
 * strips' order, so the tier column groups the rows by the chip they show.
 *
 * @param {string} tier the tier as stored on the record
 * @param {string|null} role the role, or null
 * @returns {number}
 */
export const bucketRank = (tier, role) =>
  ROLE_BUCKETS.includes(role) ? TIERS.length + ROLE_BUCKETS.indexOf(role) : tierRank(tier)

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
