import { describe, it, expect } from 'vitest'

import { QUEUE_REASONS, queueReason, queueRows, queueTooltip } from '@/lib/verificationQueue'

// The verification queue's joins and words (MAS-329). The ranking is the
// server's (peak_assignments/verification_queue.py); here, that the ledger shows
// its order and says why each row is there.

const ROWS = [
  { peak_assignment_id: 'a', sample_peak_id: 'pa' },
  { peak_assignment_id: 'b', sample_peak_id: 'pb' },
  { peak_assignment_id: 'c', sample_peak_id: 'pc' }
]

describe('queueRows', () => {
  it("puts the ledger's rows in the queue's order, each with its entry", () => {
    const entries = [
      { peak_assignment_id: 'c', reason: 'stale' },
      { peak_assignment_id: 'a', reason: 'band_edge' }
    ]
    const rows = queueRows(ROWS, entries)

    expect(rows.map((row) => row.peak_assignment_id)).toEqual(['c', 'a'])
    expect(rows.map((row) => row.queueRank)).toEqual([0, 1])
    expect(rows[0].queueEntry).toBe(entries[0])
    expect(rows[0].sample_peak_id).toBe('pc')
  })

  it('skips an entry whose row the loaded run does not hold, without a gap in rank', () => {
    const rows = queueRows(ROWS, [
      { peak_assignment_id: 'gone', reason: 'band_edge' },
      { peak_assignment_id: 'b', reason: 'spot_check' }
    ])

    expect(rows.map((row) => [row.peak_assignment_id, row.queueRank])).toEqual([['b', 0]])
  })

  it('answers no rows for an empty or missing queue', () => {
    expect(queueRows(ROWS, [])).toEqual([])
    expect(queueRows(ROWS, undefined)).toEqual([])
    expect(queueRows(undefined, [{ peak_assignment_id: 'a' }])).toEqual([])
  })
})

describe('queueTooltip', () => {
  it('names the tier edge and the verdicts held near it', () => {
    expect(queueTooltip({ reason: 'band_edge', near_band: 'candidate', labels_near: 0 })).toContain(
      'edge of the candidate tier, where the instrument holds no verdict yet'
    )
    expect(queueTooltip({ reason: 'band_edge', near_band: 'assigned', labels_near: 1 })).toContain(
      'assigned tier, where the instrument holds 1 verdict so far'
    )
    expect(queueTooltip({ reason: 'band_edge', near_band: 'assigned', labels_near: 7 })).toContain(
      '7 verdicts so far'
    )
  })

  it('says a spot check is random', () => {
    expect(queueTooltip({ reason: 'spot_check' })).toMatch(/random spot check/)
  })

  it('says what a later run changed, and that the verdict is kept', () => {
    const rescored = queueTooltip({
      reason: 'stale',
      stale: { kind: 'rescored', verdict: 'rejected' }
    })
    expect(rescored).toContain('The rejected verdict was given before this run')
    expect(rescored).toContain('The verdict is kept')

    const reassigned = queueTooltip({
      reason: 'stale',
      stale: { kind: 'reassigned', verdict: 'confirmed', assigned_formula: 'C6H12O6' }
    })
    expect(reassigned).toContain('A confirmed verdict on C6H12O6')
    expect(reassigned).toContain('another formula')
  })

  it('names no probability and no evidence value', () => {
    for (const entry of [
      { reason: 'band_edge', near_band: 'candidate', labels_near: 3 },
      { reason: 'spot_check' },
      { reason: 'stale', stale: { kind: 'rescored', verdict: 'confirmed' } }
    ]) {
      expect(queueTooltip(entry)).not.toMatch(/%|P\(correct\)|\d\.\d/)
    }
  })
})

describe('queueReason', () => {
  it('has a label and an icon for each reason the server gives', () => {
    for (const reason of ['stale', 'band_edge', 'spot_check']) {
      expect(queueReason({ reason })).toBe(QUEUE_REASONS[reason])
      expect(QUEUE_REASONS[reason].icon).toMatch(/^ph ph-/)
    }
    expect(queueReason({ reason: 'unknown' })).toBeNull()
    expect(queueReason(null)).toBeNull()
  })
})
