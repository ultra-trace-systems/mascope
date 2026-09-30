import { describe, it, expect } from 'vitest'

import { isIsotopeLine } from '@/lib/isotopeLines'

// Which rows fold under another. An analyte's isotopologue always does; a source
// ion's line keeps the reagent role, so it is its owner that makes it a line.
describe('isIsotopeLine', () => {
  it("is an analyte's isotopologue, with or without an owner in the ledger", () => {
    expect(isIsotopeLine({ role: 'iso_child', owner_peak_assignment_id: 'pa-m0' })).toBe(true)
    expect(isIsotopeLine({ role: 'iso_child', owner_peak_assignment_id: null })).toBe(true)
  })

  it("is a source ion's line that names the ion's row", () => {
    expect(isIsotopeLine({ role: 'reagent', owner_peak_assignment_id: 'pa-ion' })).toBe(true)
  })

  it('is not an ion of its own, nor any other row', () => {
    expect(isIsotopeLine({ role: 'reagent', owner_peak_assignment_id: null })).toBe(false)
    expect(isIsotopeLine({ role: 'reagent' })).toBe(false)
    expect(isIsotopeLine({ role: 'M0' })).toBe(false)
    expect(isIsotopeLine({ role: 'artifact' })).toBe(false)
    expect(isIsotopeLine({ role: 'unassigned' })).toBe(false)
    expect(isIsotopeLine(null)).toBe(false)
  })
})
