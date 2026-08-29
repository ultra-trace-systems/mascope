import { describe, it, expect } from 'vitest'

import { isotopeOfHit } from '@/lib/panes/PanePeakAssign/isotope.js'

// Assigning a re-search hit to a peak has to say WHICH isotopologue of the
// candidate's ion that peak is. The search scores whole ions, so a heavy-isotope
// satellite is a perfectly ordinary hit - and committing one as an 'M0' would
// record a compound's satellite as the compound's main peak, which the tier
// histogram, the batch consensus and a family-scoped verdict would all believe.

/** A candidate's predicted isotope pattern: M0, M+1, M+2 by decreasing abundance. */
const PATTERN = [
  { mz: 180.0634, relative_abundance: 1.0, target_isotope_formula: 'C6H12O6' },
  { mz: 181.0668, relative_abundance: 0.067, target_isotope_formula: '[13C]C5H12O6' },
  { mz: 182.0692, relative_abundance: 0.009, target_isotope_formula: '[13C2]C4H12O6' }
]

const hit = (searchedMz, children = PATTERN) => ({
  children,
  cheminfo: { target_isotope_mz: searchedMz }
})

describe('isotopeOfHit', () => {
  it('calls the most abundant isotopologue M0', () => {
    expect(isotopeOfHit(hit(180.0634))).toEqual({
      label: 'M0',
      formula: 'C6H12O6'
    })
  })

  it('labels a satellite by its nominal offset from M0', () => {
    expect(isotopeOfHit(hit(181.0668)).label).toBe('M+1')
    expect(isotopeOfHit(hit(182.0692)).label).toBe('M+2')
  })

  it('carries the matched isotopologue formula, not the ion-level one', () => {
    expect(isotopeOfHit(hit(181.0668)).formula).toBe('[13C]C5H12O6')
  })

  // Abundance decides which one is M0, not position or m/z order: a pattern
  // whose main isotopologue is not the lightest is ordinary (chlorine, bromine),
  // and reading the first row as M0 would label the real main peak 'M-2'.
  it('takes M0 from abundance, not from the lightest isotopologue', () => {
    const heavyMain = [
      { mz: 100.0, relative_abundance: 0.24 },
      { mz: 102.0, relative_abundance: 1.0 }
    ]
    expect(isotopeOfHit(hit(102.0, heavyMain)).label).toBe('M0')
    expect(isotopeOfHit(hit(100.0, heavyMain)).label).toBe('M-2')
  })

  // The searched m/z is the observed peak's, so it sits near the predicted
  // isotope rather than on it. Nearest wins.
  it('matches the nearest predicted isotopologue, not an exact m/z', () => {
    expect(isotopeOfHit(hit(181.0669)).label).toBe('M+1')
  })

  it('falls back to M0 when there is no pattern to place the peak in', () => {
    expect(isotopeOfHit({ children: [] })).toEqual({ label: 'M0', formula: null })
    expect(isotopeOfHit({})).toEqual({ label: 'M0', formula: null })
    expect(isotopeOfHit(null)).toEqual({ label: 'M0', formula: null })
  })

  // A hit with a pattern but no searched m/z means the main isotopologue: there
  // is nothing to place, and guessing a satellite would be worse than saying M0.
  it('reads a hit with no searched m/z as the main isotopologue', () => {
    expect(isotopeOfHit({ children: PATTERN })).toEqual({
      label: 'M0',
      formula: 'C6H12O6'
    })
  })
})
