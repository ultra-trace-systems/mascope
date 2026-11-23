import { describe, it, expect } from 'vitest'

import {
  batchLineLabel,
  batchLineName,
  offsetLabel,
  sampleLineLabel,
  verdictRank
} from '@/lib/ledger/rows.js'

describe('offsetLabel', () => {
  it('names a line by its nominal offset from the M0', () => {
    expect(offsetLabel(101.05, 100.05)).toBe('M+1')
    expect(offsetLabel(102.04, 100.05)).toBe('M+2')
    expect(offsetLabel(99.04, 100.05)).toBe('M-1')
    expect(offsetLabel(100.06, 100.05)).toBe('M0')
  })

  it('names nothing when either m/z is unknown', () => {
    expect(offsetLabel(null, 100)).toBe('')
    expect(offsetLabel(101, undefined)).toBe('')
  })
})

describe('sampleLineLabel', () => {
  const M0 = { sample_peak_mz: 180.06, ion_formula: 'C6H12O6Br-' }

  it('names the substitution the row records', () => {
    expect(sampleLineLabel({ isotope_formula: '[13C]C5H12O6', sample_peak_mz: 181.06 }, M0)).toBe(
      '[13C]'
    )
  })

  it("brackets a source ion's line label, as an analyte's reads", () => {
    expect(sampleLineLabel({ isotope_label: '81Br', sample_peak_mz: 182.06 }, M0)).toBe('[81Br]')
  })

  it('falls back to the offset from the M0 only where nothing names the line', () => {
    expect(sampleLineLabel({ sample_peak_mz: 182.06 }, M0)).toBe('M+2')
    // An offset the engine wrote is kept as it is.
    expect(sampleLineLabel({ isotope_label: 'M+1', sample_peak_mz: 181.06 }, M0)).toBe('M+1')
    expect(sampleLineLabel({}, null)).toBe('iso')
  })
})

describe('batchLineName and batchLineLabel', () => {
  const PARENT = {
    batch_peak_id: 'bp-p',
    mz: 200.0,
    diagnostic_isotopologues: {
      predicted: [
        { label: '34S', batch_peak_id: 'bp-s', stamped: false },
        { label: '37Cl', batch_peak_id: 'bp-other', reason: 'another_parent' },
        { label: '18O', batch_peak_id: 'bp-o', reason: 'unresolved' }
      ]
    },
    merged_isotopologues: { lines: [{ label: '13C', batch_peak_id: 'bp-c' }] }
  }
  const line = (id, mz = 202.0) => ({ batch_peak_id: id, mz })

  it('names a line by the stamp first, then the strip', () => {
    const stamp = { label: '81Br', batch_peak_id: 'bp-p' }
    const strip = { label: '13C', batch_peak_id: 'bp-p' }
    expect(batchLineName(line('bp-x'), PARENT, { stamp, strip })).toMatchObject({
      source: 'stamp',
      label: '81Br'
    })
    expect(batchLineName(line('bp-x'), PARENT, { strip })).toMatchObject({
      source: 'strip',
      label: '13C'
    })
    expect(batchLineLabel(line('bp-x'), PARENT, { stamp })).toBe('[81Br]')
  })

  it("names a line from the family's own records of its lines", () => {
    expect(batchLineLabel(line('bp-s'), PARENT)).toBe('[34S]')
    expect(batchLineLabel(line('bp-c'), PARENT)).toBe('[13C]')
    expect(batchLineName(line('bp-c'), PARENT).source).toBe('family')
  })

  // A predicted line the stamping read as another parent's, or could not
  // separate, says nothing about this row.
  it("ignores a record that is about another family's line", () => {
    expect(batchLineName(line('bp-other'), PARENT)).toBeNull()
    expect(batchLineName(line('bp-o'), PARENT)).toBeNull()
    expect(batchLineLabel(line('bp-other', 202.0), PARENT)).toBe('M+2')
  })

  it('falls back to the offset from the M0 where nothing names the line', () => {
    expect(batchLineLabel(line('bp-z', 201.0), PARENT)).toBe('M+1')
    expect(batchLineLabel(line('bp-z', 201.0), null)).toBe('iso')
  })
})

describe('verdictRank', () => {
  it('ranks confirmed first and an unjudged row null, so it sorts last', () => {
    expect(verdictRank({ verdict: 'confirmed' })).toBe(0)
    expect(verdictRank({ verdict: 'rejected' })).toBe(1)
    expect(verdictRank({ verdict: 'unsure' })).toBe(2)
    expect(verdictRank({ verdict: 'something else' })).toBe(3)
    expect(verdictRank(null)).toBeNull()
  })
})
