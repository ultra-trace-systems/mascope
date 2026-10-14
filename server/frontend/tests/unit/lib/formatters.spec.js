import { describe, it, expect } from 'vitest'

import { num } from '@/lib/formatters'

describe('num formatters', () => {
  it('mz pads to two integer digits and four decimals', () => {
    expect(num.mz.format(34.2146)).toBe('34.2146')
    expect(num.mz.format(5.1)).toBe('05.1000')
    expect(num.mz.format(1234.56789)).toBe('1,234.5679')
  })

  it('mzError keeps two decimals', () => {
    expect(num.mzError.format(1.2345)).toBe('1.23')
    expect(num.mzError.format(-0.5)).toBe('-0.50')
  })

  it('mzCorrection signs a correction, and none that rounds to zero', () => {
    expect(num.mzCorrection.format(-11.934)).toBe('-11.93')
    expect(num.mzCorrection.format(20.402)).toBe('+20.40')
    expect(num.mzCorrection.format(-0.004)).toBe('0.00')
    expect(num.mzCorrection.format(0.004)).toBe('0.00')
  })

  it('peakIntensity uses scientific notation with four significant digits', () => {
    expect(num.peakIntensity.format(123456)).toBe('1.235E5')
    expect(num.peakIntensity.format(0.000123)).toBe('1.230E-4')
  })

  it('relative abundance renders as percent', () => {
    expect(num.relativeAbundance.format(0.123456)).toBe('12.346%')
    expect(num.relativeAbundanceError.format(0.1)).toBe('10.00%')
    expect(num.ticFraction.format(0.055)).toBe('5.50%')
  })
})
