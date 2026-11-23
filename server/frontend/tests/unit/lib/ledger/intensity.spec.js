import { describe, it, expect } from 'vitest'

import { compactNumber, intensityBarShare, intensityDomain } from '@/lib/ledger/intensity.js'

// The intensity cell's number and bar (docs/dev/assignment_ledger_and_card.md,
// section 3.2): `8.31k`, `1.20M`, never `6.751E2`.

describe('compactNumber', () => {
  it('writes 19, 8,310 and 1,200,000 as 19, 8.31k and 1.20M', () => {
    expect(compactNumber(19)).toBe('19')
    expect(compactNumber(8310)).toBe('8.31k')
    expect(compactNumber(1_200_000)).toBe('1.20M')
  })

  it('keeps three significant digits, the trailing zeros of a scaled one', () => {
    expect(compactNumber(675.1)).toBe('675')
    expect(compactNumber(12_000)).toBe('12.0k')
    expect(compactNumber(123_456)).toBe('123k')
    expect(compactNumber(0.5321)).toBe('0.532')
    expect(compactNumber(4.2e9)).toBe('4.20G')
    expect(compactNumber(3.1e13)).toBe('31.0T')
  })

  // Three digits can round a number up into the next prefix.
  it('rolls a number that rounds to a thousand into the next prefix', () => {
    expect(compactNumber(999.6)).toBe('1.00k')
    expect(compactNumber(999_950)).toBe('1.00M')
    expect(compactNumber(999.4)).toBe('999')
  })

  it('writes zero and a negative number, and nothing for no number', () => {
    expect(compactNumber(0)).toBe('0')
    expect(compactNumber(-8310)).toBe('-8.31k')
    expect(compactNumber(null)).toBe('')
    expect(compactNumber(undefined)).toBe('')
    expect(compactNumber(Number.NaN)).toBe('')
    expect(compactNumber(Infinity)).toBe('')
  })
})

describe('the log-scale bar', () => {
  it("spans the scope's positive intensities", () => {
    expect(intensityDomain([5, null, 0, 1e6, -3, 200])).toEqual([5, 1e6])
    expect(intensityDomain([null, 0])).toBeNull()
  })

  it('draws a decade as a step, the faintest a sliver and the brightest whole', () => {
    const domain = [10, 1e5]
    expect(intensityBarShare(10, domain)).toBeCloseTo(0.04)
    expect(intensityBarShare(1e3, domain)).toBeCloseTo(0.04 + 0.96 * 0.5)
    expect(intensityBarShare(1e5, domain)).toBe(1)
  })

  it('draws no bar for no intensity, and a whole one where all rows are alike', () => {
    expect(intensityBarShare(null, [10, 100])).toBe(0)
    expect(intensityBarShare(0, [10, 100])).toBe(0)
    expect(intensityBarShare(50, null)).toBe(0)
    expect(intensityBarShare(50, [50, 50])).toBe(1)
    // Outside the domain (a row the domain was not read over): held to it.
    expect(intensityBarShare(1, [10, 100])).toBeCloseTo(0.04)
    expect(intensityBarShare(1e4, [10, 100])).toBe(1)
  })
})
