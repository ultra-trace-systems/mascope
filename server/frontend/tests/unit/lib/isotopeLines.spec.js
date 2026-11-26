import { describe, it, expect } from 'vitest'

import { isIsotopeLine } from '@/lib/isotopeLines'

// Which rows fold under another: an analyte's isotopologue or a source ion's
// line, and only one that names the row it belongs to.
describe('isIsotopeLine', () => {
  it("is an analyte's isotopologue that names its M0", () => {
    expect(isIsotopeLine({ role: 'iso_child', owner_peak_assignment_id: 'pa-m0' })).toBe(true)
  })

  // One assigned by hand on a sample whose run holds no M0 of its formula
  // (mascope#3661), or one whose M0 the run gave to another ion: it has no row
  // to fold under, so it stands as a row of its own.
  it('is not an isotopologue with no owner', () => {
    expect(isIsotopeLine({ role: 'iso_child', owner_peak_assignment_id: null })).toBe(false)
    expect(isIsotopeLine({ role: 'iso_child' })).toBe(false)
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
