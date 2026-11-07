import { describe, it, expect } from 'vitest'

import { seriesTooltip } from '@/lib/batchSeries'

const SERIES = {
  formula: 'C9H12O5',
  unit: 'CH2',
  units: ['CH2'],
  step: 1,
  depth: 1,
  root_formula: 'C8H10O5',
  has_anchor: true
}

describe('seriesTooltip', () => {
  it('names the step and the assigned peak the chain starts at', () => {
    expect(seriesTooltip(SERIES)).toBe(
      "In the batch's CH2 series: one CH2 heavier than C8H10O5, which the batch assigns."
    )
  })

  it('counts the steps and says which way', () => {
    expect(seriesTooltip({ ...SERIES, step: -1, depth: 2 })).toContain('two CH2 lighter than')
    expect(seriesTooltip({ ...SERIES, depth: 4 })).toContain(': 4 CH2 heavier than')
  })

  it('names the other series that propose the same formula', () => {
    expect(seriesTooltip({ ...SERIES, units: ['CH2', 'CO', 'O'] })).toMatch(
      / It is in the CO and O series too\.$/
    )
  })

  it('says what it can of a record without its chain', () => {
    expect(seriesTooltip({ unit: 'O' })).toBe("In the batch's O series.")
    expect(seriesTooltip({ ...SERIES, depth: 0 })).toBe("In the batch's CH2 series.")
  })

  it('is empty for no series', () => {
    expect(seriesTooltip(null)).toBe('')
    expect(seriesTooltip(undefined)).toBe('')
  })
})
