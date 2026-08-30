import { describe, it, expect } from 'vitest'

import { isotopeOfHit, curationBodyForHit, hitKey } from '@/lib/panes/PanePeakAssign/searchHit.js'

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


// The other half of the translation: what actually gets sent when a hit is
// committed to the focused peak. This is the request contract with the curation
// endpoint, so it is pinned here rather than left to a pane mount.
describe('curationBodyForHit', () => {
  const HIT = {
    target_compound_formula: 'C6H12O6',
    target_ion_formula: 'C6H13O6+',
    ionization_mechanism_id: 'mech-1',
    fit_score: 0.87,
    plausibility: 0.91,
    children: PATTERN,
    cheminfo: {
      target_isotope_mz: 180.0634,
      target_isotope_mz_error_ppm: -1.4
    }
  }

  it('sends the composition, its adduct and the measured scores', () => {
    expect(curationBodyForHit(HIT)).toEqual({
      action: 'set_assignment',
      assigned_formula: 'C6H12O6',
      ionization_mechanism_id: 'mech-1',
      ion_formula: 'C6H13O6+',
      isotope_label: 'M0',
      isotope_formula: 'C6H12O6',
      fit_score: 0.87,
      mz_error_ppm: -1.4
    })
  })

  // Plausibility is a pure function of the formula, so the server computes it
  // from what it commits. Sending the table's number would be a claim about
  // chemistry made by the client, and the endpoint does not accept one.
  it('does not send a plausibility, even though the hit carries one', () => {
    expect(curationBodyForHit(HIT)).not.toHaveProperty('plausibility')
  })

  it('carries the isotopologue label through, so a satellite stays a satellite', () => {
    const body = curationBodyForHit({ ...HIT, cheminfo: { target_isotope_mz: 181.0668 } })

    expect(body.isotope_label).toBe('M+1')
    expect(body.isotope_formula).toBe('[13C]C5H12O6')
  })

  // A hit that is missing an optional field sends null rather than undefined,
  // which JSON would drop silently.
  it('nulls what the hit does not carry', () => {
    const body = curationBodyForHit({
      target_compound_formula: 'CH4',
      ionization_mechanism_id: 'mech-2'
    })

    expect(body.ion_formula).toBeNull()
    expect(body.fit_score).toBeNull()
    expect(body.mz_error_ppm).toBeNull()
    expect(body.isotope_label).toBe('M0')
  })
})

// The results table keys rows by formula alone, so two adducts of one
// composition are one dataKey - and per-row busy state keyed on that would
// spin both rows at once.
describe('hitKey', () => {
  it('separates the same composition found under two adducts', () => {
    const a = { target_compound_formula: 'C6H12O6', ionization_mechanism_id: 'mech-1' }
    const b = { target_compound_formula: 'C6H12O6', ionization_mechanism_id: 'mech-2' }

    expect(hitKey(a)).not.toBe(hitKey(b))
  })

  it('is stable for the same hit', () => {
    const hit = { target_compound_formula: 'C6H12O6', ionization_mechanism_id: 'mech-1' }

    expect(hitKey(hit)).toBe(hitKey({ ...hit }))
  })
})
