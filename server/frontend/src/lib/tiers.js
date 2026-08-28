/**
 * Confidence tiers of a peak assignment, defined once.
 *
 * The same four values are ranked, ordered and labelled by several surfaces -
 * the per-sample assignment ledger, the batch-peak ledger, BaseTierTag - and
 * every copy of the order has to agree, or the tier column sorts by confidence
 * in one table and alphabetically in the next. Keeping the fact here also makes
 * renaming a tier one edit rather than a grep.
 *
 * `reagent` and `artifact` are *roles*, not tiers: a reagent peak still carries
 * one of these four tiers. A surface that shows a reagent bucket (the sample
 * ledger's histogram strip) derives it from `row.role`, so it is deliberately
 * absent here.
 */

// Confidence order: identified first, unassigned last.
export const TIER_ORDER = ['identified', 'candidate', 'below_assignability', 'unassigned']

// tier -> rank, so a sortable tier column orders by confidence rather than
// alphabetically (which otherwise puts "unassigned" above "identified").
export const TIER_RANK = Object.fromEntries(TIER_ORDER.map((tier, index) => [tier, index]))

// An unknown or missing tier ranks last, alongside the unassigned peaks.
export const UNRANKED = TIER_ORDER.length - 1

/** Whether a value is one of the four tiers.
 *
 *  Own properties only: a plain `tier in TIER_RANK` also answers yes to
 *  `toString` and every other name on Object.prototype, and `TIER_RANK[tier]`
 *  would then hand back a function rather than a rank. */
export const isTier = (tier) => Object.hasOwn(TIER_RANK, tier)

/** Rank of a tier value, tolerant of nulls and values this build does not know. */
export const tierRank = (tier) => (isTier(tier) ? TIER_RANK[tier] : UNRANKED)
