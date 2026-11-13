import { describe, expect, it } from 'vitest'

import { blendOf, blendSummary, blendTooltip } from '@/lib/batchBlend'
import { batchPeakLabel } from '@/lib/batchChart'

const record = (extra = {}) => ({
  formulas: ['C7H14O5', 'C6H12NO'],
  ions: ['C7H13O5-', 'C6H12NO4-'],
  n: [488, 20],
  axis: 'time',
  intensity: { p: 0.2, separation: 0.6, separates: false, brighter: 'C7H14O5' },
  time: { p: 4.3e-71, runs: 4, expected_runs: 39.43, separates: true, earlier: 'C6H12NO' },
  ...extra
})

describe('batchBlend', () => {
  it('reads the reason off a batch peak, null where none stands', () => {
    expect(blendOf({ blend: record() })).toEqual(record())
    expect(blendOf({ blend: null })).toBeNull()
    expect(blendOf({})).toBeNull()
    expect(blendOf(null)).toBeNull()
    expect(blendOf({ blend: { formulas: ['C7H14O5'] } })).toBeNull()
  })

  it('names the two formulas and the axis that parts them', () => {
    expect(blendSummary(record())).toBe(
      'Probable blend of C7H14O5 and C6H12NO, parted by acquisition time'
    )
    expect(blendSummary(record({ axis: 'intensity' }))).toBe(
      'Probable blend of C7H14O5 and C6H12NO, parted by intensity'
    )
    expect(blendSummary(null)).toBe('')
  })

  it('says which formula holds the early samples, and how few runs they come in', () => {
    const text = blendTooltip(record())
    expect(text).toContain(
      'C7H14O5 (C7H13O5-) in 488 samples and C6H12NO (C6H12NO4-) in 20 samples'
    )
    expect(text).toContain('C6H12NO holds the early samples and C7H14O5 the late ones')
    expect(text).toContain('4 runs in acquisition order, where interleaved they would give 39')
    expect(text).toContain('p < 0.001')
    // An axis that does not part them is not described.
    expect(text).not.toContain('bright samples')
    expect(text).toContain('The consensus and its tier are unchanged')
  })

  it('says which formula holds the bright samples, and how cleanly', () => {
    const text = blendTooltip(
      record({
        axis: 'intensity',
        intensity: { p: 0.0034, separation: 0.07, separates: true, brighter: 'C6H12NO' },
        time: null
      })
    )
    expect(text).toContain('C6H12NO holds the bright samples and C7H14O5 the dim ones')
    expect(text).toContain('93% of their pairs order that way (p = 0.0034)')
    expect(text).not.toContain('runs in acquisition order')
  })

  it('describes both axes where both part them', () => {
    const text = blendTooltip(
      record({ intensity: { p: 1e-9, separation: 0.98, separates: true, brighter: 'C7H14O5' } })
    )
    expect(text).toContain('holds the bright samples')
    expect(text).toContain('holds the early samples')
  })

  it('writes a record from a server without ions or counts plainly', () => {
    const text = blendTooltip(record({ ions: undefined, n: undefined }))
    expect(text).toContain('The samples read C7H14O5 and C6H12NO.')
    expect(blendTooltip(null)).toBe('')
  })
})

describe('batchPeakLabel', () => {
  it('names a blended batch peak by both its formulas, the consensus first', () => {
    expect(batchPeakLabel({ consensus_formula: 'C7H14O5', mz: 177.077, blend: record() })).toBe(
      'C7H14O5 + C6H12NO'
    )
  })

  it('names any other batch peak by its formula, or its m/z', () => {
    expect(batchPeakLabel({ consensus_formula: 'C7H14O5', mz: 177.077, blend: null })).toBe(
      'C7H14O5'
    )
    expect(batchPeakLabel({ consensus_formula: null, mz: 177.07706 })).toBe('m/z 177.0771')
  })
})
