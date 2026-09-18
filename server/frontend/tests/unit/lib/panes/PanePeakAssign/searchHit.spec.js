import { describe, it, expect } from 'vitest'

import {
  isotopeOfHit,
  curationBodyForHit,
  canCurateHit,
  hitKey
} from '@/lib/panes/PanePeakAssign/searchHit.js'

// Assigning a re-search hit to a peak has to say WHICH isotopologue of the
// candidate's ion that peak is. The search scores whole ions, so a heavy-isotope
// isotopologue is a perfectly ordinary hit - and committing one as an 'M0' would
// record a compound's isotopologue as the compound's main peak, which the tier
// histogram, the batch consensus and a family-scoped verdict would all believe.

/** A candidate's predicted isotope pattern: M0, M+1, M+2 by decreasing abundance. */
const PATTERN = [
  { mz: 180.0634, relative_abundance: 1.0, target_isotope_formula: 'C6H12O6' },
  { mz: 181.0668, relative_abundance: 0.067, target_isotope_formula: '[13C]C5H12O6' },
  { mz: 182.0692, relative_abundance: 0.009, target_isotope_formula: '[13C]2C4H12O6' }
]

const hit = (searchedMz, children = PATTERN) => ({
  children,
  cheminfo: { target_isotope_mz: searchedMz }
})

describe('isotopeOfHit', () => {
  it('calls the monoisotopic isotopologue M0', () => {
    expect(isotopeOfHit(hit(180.0634))).toEqual({
      label: 'M0',
      formula: 'C6H12O6'
    })
  })

  it('labels an isotopologue by its nominal offset from M0', () => {
    expect(isotopeOfHit(hit(181.0668)).label).toBe('M+1')
    expect(isotopeOfHit(hit(182.0692)).label).toBe('M+2')
  })

  it('carries the matched isotopologue formula, not the ion-level one', () => {
    expect(isotopeOfHit(hit(181.0668)).formula).toBe('[13C]C5H12O6')
  })

  // The monoisotopic isotopologue decides, not abundance. A pattern whose
  // tallest peak is not its lightest is ordinary (chlorine, bromine), and an
  // isotope table counts from the lightest peak of the cluster: Br3- reads M0
  // at 236.76 with the taller 238.75 as its M+2.
  it('takes M0 from the monoisotopic isotopologue, not the most abundant one', () => {
    const bromine = [
      { mz: 236.7551, relative_abundance: 0.34, target_isotope_formula: 'Br3-' },
      { mz: 238.753, relative_abundance: 1.0, target_isotope_formula: '[81Br]Br2-' }
    ]
    expect(isotopeOfHit(hit(236.7551, bromine)).label).toBe('M0')
    expect(isotopeOfHit(hit(238.753, bromine)).label).toBe('M+2')
  })

  // Nothing marks the substitution, so there is no way to tell the two apart:
  // the lightest row stands in, the same fallback `monoisotopic_row` takes.
  it('falls back to the lightest isotopologue when no formula is marked', () => {
    const unmarked = [
      { mz: 100.0, relative_abundance: 0.24 },
      { mz: 102.0, relative_abundance: 1.0 }
    ]
    expect(isotopeOfHit(hit(100.0, unmarked)).label).toBe('M0')
    expect(isotopeOfHit(hit(102.0, unmarked)).label).toBe('M+2')
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
  // is nothing to place, and guessing an isotopologue would be worse than saying M0.
  it('reads a hit with no searched m/z as the main isotopologue', () => {
    expect(isotopeOfHit({ children: PATTERN })).toEqual({
      label: 'M0',
      formula: 'C6H12O6'
    })
  })
})

// The brackets alone cannot find a labelled ion's M0, because a labelled
// reagent's atom is bracketed like any substituted isotope: the 15N-nitrate ion
// C9H16O7^N- is measured by its [15N]C9H16O7- line, and the one formula without
// a bracket, C9H16NO7-, is the reagent's unlabelled remainder - 2% of that line
// and one mass unit below it. The ion formula says which brackets are labels,
// and the search carries it on the hit, not on the hit's isotopologues.
describe('isotopeOfHit with the ion formula', () => {
  /** A hit on the ion `ionFormula`, searched at `searchedMz` in its pattern. */
  const ionHit = (ionFormula, searchedMz, children) => ({
    ...hit(searchedMz, children),
    target_ion_formula: ionFormula
  })

  it('counts a 15N-labelled ion from its labelled line, with the remainder at M-1', () => {
    const labelled = [
      { mz: 250.0932, relative_abundance: 0.0204, target_isotope_formula: 'C9H16NO7-' },
      { mz: 251.0903, relative_abundance: 1.0, target_isotope_formula: '[15N]C9H16O7-' },
      { mz: 252.0936, relative_abundance: 0.0973, target_isotope_formula: '[13C][15N]C8H16O7-' }
    ]
    const at = (mz) => isotopeOfHit(ionHit('C9H16O7^N-', mz, labelled))

    expect(at(251.0903)).toEqual({ label: 'M0', formula: '[15N]C9H16O7-' })
    expect(at(250.0932)).toEqual({ label: 'M-1', formula: 'C9H16NO7-' })
    expect(at(252.0936)).toEqual({ label: 'M+1', formula: '[13C][15N]C8H16O7-' })
  })

  // The 15N nitric acid-nitrate cluster carries two labelled atoms. A line that
  // names the label once is not its M0 but the M-1, with one atom unlabelled.
  it('needs every labelled atom of a two-label ion at its label', () => {
    const cluster = [
      { mz: 124.984, relative_abundance: 0.0004, target_isotope_formula: 'HN2O6-' },
      { mz: 125.981, relative_abundance: 0.0408, target_isotope_formula: '[15N]HNO6-' },
      { mz: 126.9781, relative_abundance: 1.0, target_isotope_formula: '[15N]2HO6-' },
      { mz: 128.9823, relative_abundance: 0.0123, target_isotope_formula: '[15N]2[18O]HO5-' }
    ]
    const at = (mz) => isotopeOfHit(ionHit('HO6^N2-', mz, cluster)).label

    expect(at(126.9781)).toBe('M0')
    expect(at(125.981)).toBe('M-1')
    expect(at(124.984)).toBe('M-2')
    expect(at(128.9823)).toBe('M+2')
  })

  // At a low resolution the labelled line and the remainder's 13C line, 6 mDa
  // apart, are one line, and the generator names both.
  it('reads a merged low-resolution line as the M0 when one of its names is', () => {
    const merged = '[15N]C9H16O7-/[13C]C8H16NO7-'
    const lowResolution = [
      { mz: 250.0932, relative_abundance: 0.0204, target_isotope_formula: 'C9H16NO7-' },
      { mz: 251.0903, relative_abundance: 1.0, target_isotope_formula: merged }
    ]
    const at = (mz) => isotopeOfHit(ionHit('C9H16O7^N-', mz, lowResolution))

    expect(at(251.0903)).toEqual({ label: 'M0', formula: merged })
    expect(at(250.0932).label).toBe('M-1')
  })

  // An ion that names no label keeps the line without a bracket as its M0. For
  // bromoform with bromide that is the lightest line of the cluster, while the
  // tallest, with two of the four bromines at 81Br, is its M+4.
  it('counts an unlabelled bromine cluster from its lightest line, not its tallest', () => {
    const bromine = [
      { mz: 328.6817, relative_abundance: 0.176, target_isotope_formula: 'CHBr4-' },
      { mz: 330.6797, relative_abundance: 0.685, target_isotope_formula: '[81Br]CHBr3-' },
      { mz: 332.6776, relative_abundance: 1.0, target_isotope_formula: '[81Br]2CHBr2-' },
      { mz: 334.6756, relative_abundance: 0.649, target_isotope_formula: '[81Br]3CHBr-' },
      { mz: 336.6735, relative_abundance: 0.158, target_isotope_formula: '[81Br]4CH-' }
    ]
    const at = (mz) => isotopeOfHit(ionHit('CHBr4-', mz, bromine))

    expect(at(328.6817)).toEqual({ label: 'M0', formula: 'CHBr4-' })
    expect(at(332.6776)).toEqual({ label: 'M+4', formula: '[81Br]2CHBr2-' })
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

  it('carries the isotopologue label through, so an isotopologue stays one', () => {
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

  // The mechanism is required by the endpoint, so this body cannot succeed -
  // but a dropped key is refused as "field required", which names nothing the
  // caller can act on, while an explicit null points at the field.
  it('states a missing mechanism rather than dropping the key', () => {
    const body = curationBodyForHit({ target_compound_formula: 'CH4' })

    expect(body).toHaveProperty('ionization_mechanism_id')
    expect(body.ionization_mechanism_id).toBeNull()
  })
})

// A composition committed under no adduct is half an assignment: the
// verification identity is (peak, formula, mechanism), so such a row could
// never carry a verdict, and `set_assignment` refuses it with a 422 the user
// would only ever meet as a toast. The control is withheld instead.
describe('canCurateHit', () => {
  it('accepts a hit that names a composition and its adduct', () => {
    expect(
      canCurateHit({ target_compound_formula: 'C6H12O6', ionization_mechanism_id: 'mech-1' })
    ).toBe(true)
  })

  it('refuses a hit with no ionization mechanism', () => {
    expect(canCurateHit({ target_compound_formula: 'C6H12O6' })).toBe(false)
    expect(
      canCurateHit({ target_compound_formula: 'C6H12O6', ionization_mechanism_id: null })
    ).toBe(false)
    expect(canCurateHit({ target_compound_formula: 'C6H12O6', ionization_mechanism_id: '' })).toBe(
      false
    )
  })

  it('refuses a hit with no composition to commit', () => {
    expect(canCurateHit({ ionization_mechanism_id: 'mech-1' })).toBe(false)
    expect(canCurateHit({})).toBe(false)
    expect(canCurateHit(null)).toBe(false)
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
