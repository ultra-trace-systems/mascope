import { describe, it, expect } from 'vitest'

import {
  FALLBACK_TIER,
  TIERS,
  TIER_META,
  TIER_RANK,
  countTiers,
  isTier,
  tierBucket,
  tierMeta,
  tierRank
} from '@/lib/tiers'

// The one place the four confidence tiers are named. Two ledgers, a chip and a
// filter read it, and the bug it exists to prevent is them disagreeing: the
// batch-peaks table used to sort the raw tier string, which put
// below_assignability above candidate.

describe('TIERS', () => {
  it('lists the four tiers in confidence order', () => {
    expect(TIERS).toEqual(['assigned', 'candidate', 'below_assignability', 'unassigned'])
  })

  it('has no reagent bucket', () => {
    // reagent/artifact are roles, orthogonal to confidence. The sample ledger
    // folds a reagent role into its own histogram bucket; that is a decision
    // about roles, not a fifth tier, and a batch peak has no role at all.
    expect(TIERS).not.toContain('reagent')
  })

  it('ranks every tier by its position, best first', () => {
    expect(TIER_RANK).toEqual({
      assigned: 0,
      candidate: 1,
      below_assignability: 2,
      unassigned: 3
    })
  })

  it('describes every tier it names', () => {
    for (const tier of TIERS) {
      expect(TIER_META[tier]).toMatchObject({
        key: tier,
        label: expect.any(String),
        severity: expect.any(String),
        icon: expect.any(String)
      })
    }
  })
})

describe('tierRank', () => {
  it('orders assigned before candidate before below before unassigned', () => {
    const shuffled = ['unassigned', 'below_assignability', 'assigned', 'candidate']
    expect([...shuffled].sort((a, b) => tierRank(a) - tierRank(b))).toEqual(TIERS)
  })

  it('sorts an unreadable tier last rather than first', () => {
    // The failure that matters: a null tier ranked 0 would head the ledger as
    // though it were the most confident row on the page.
    expect(tierRank(null)).toBe(TIER_RANK.unassigned)
    expect(tierRank(undefined)).toBe(TIER_RANK.unassigned)
    expect(tierRank('something_new')).toBe(TIER_RANK.unassigned)
  })
})

describe('isTier', () => {
  it('accepts the four tiers and nothing else', () => {
    for (const tier of TIERS) expect(isTier(tier)).toBe(true)
    expect(isTier('reagent')).toBe(false)
    expect(isTier(null)).toBe(false)
  })

  it('does not accept an inherited property name', () => {
    expect(isTier('toString')).toBe(false)
    expect(isTier('constructor')).toBe(false)
  })
})

describe('tierBucket', () => {
  it('passes a known tier through and folds anything else into the fallback', () => {
    expect(tierBucket('candidate')).toBe('candidate')
    expect(tierBucket('')).toBe(FALLBACK_TIER)
    expect(tierBucket(null)).toBe(FALLBACK_TIER)
  })

  it('does not treat an inherited property name as a tier', () => {
    // `in` walks the prototype chain, so a bare check would accept 'toString'.
    expect(tierBucket('toString')).toBe(FALLBACK_TIER)
    expect(tierRank('constructor')).toBe(TIER_RANK.unassigned)
  })
})

describe('tierMeta', () => {
  it('shortens below_assignability, which has to fit in a chip', () => {
    expect(tierMeta('below_assignability').label).toBe('below')
  })

  it('describes an unknown tier as unassigned rather than returning nothing', () => {
    expect(tierMeta('nope')).toBe(TIER_META.unassigned)
  })
})

describe('countTiers', () => {
  const batchPeaks = [
    { consensus_tier: 'assigned' },
    { consensus_tier: 'assigned' },
    { consensus_tier: 'candidate' },
    { consensus_tier: null }
  ]

  it('counts into one bucket per tier, reading the tier where the caller says', () => {
    expect(countTiers(batchPeaks, (peak) => peak.consensus_tier)).toEqual({
      assigned: 2,
      candidate: 1,
      below_assignability: 0,
      unassigned: 1
    })
  })

  it('sums to the number of records, so the strip describes the table below it', () => {
    const counts = countTiers(batchPeaks, (peak) => peak.consensus_tier)
    const total = Object.values(counts).reduce((sum, count) => sum + count, 0)
    expect(total).toBe(batchPeaks.length)
  })

  it('never invents a bucket for an unexpected value', () => {
    // The old inline version wrote `counts[tier] = ...` straight from the
    // record, so a null tier added a literal `null` key the strip could not
    // render and did not count as unassigned either.
    const counts = countTiers([{ consensus_tier: 'reagent' }], (peak) => peak.consensus_tier)
    expect(Object.keys(counts)).toEqual(TIERS)
    expect(counts.unassigned).toBe(1)
  })

  it('reads `tier` by default and tolerates no records at all', () => {
    expect(countTiers([{ tier: 'candidate' }]).candidate).toBe(1)
    expect(countTiers(undefined)).toEqual({
      assigned: 0,
      candidate: 0,
      below_assignability: 0,
      unassigned: 0
    })
  })
})
