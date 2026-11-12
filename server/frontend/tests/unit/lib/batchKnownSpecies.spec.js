import { describe, expect, it } from 'vitest'

import {
  knownSpeciesSummary,
  knownSpeciesTooltip,
  knownSpeciesVerdict
} from '@/lib/batchKnownSpecies'

const record = (extra = {}) => ({
  species: { kind: 'library', label: '[(CH4N2O)3+H]+', ion_formula: 'C3H13N6O3+' },
  verdict: 'present',
  files: 6,
  present: 5,
  test_held: 1,
  held: null,
  members_rewritten: 4,
  files_by_reason: {},
  ...extra
})

describe('batchKnownSpecies', () => {
  it('reads the verdict off a batch peak, null where none stands', () => {
    expect(knownSpeciesVerdict({ known_species: record() })).toBe('present')
    expect(knownSpeciesVerdict({ known_species: null })).toBeNull()
    expect(knownSpeciesVerdict(null)).toBeNull()
  })

  it('names the species and the verdict', () => {
    expect(knownSpeciesSummary(record())).toBe('[(CH4N2O)3+H]+: present across the batch')
    expect(knownSpeciesSummary(record({ verdict: 'absent' }))).toBe(
      '[(CH4N2O)3+H]+: not decided present'
    )
    expect(knownSpeciesSummary(record({ verdict: 'unpooled' }))).toBe(
      '[(CH4N2O)3+H]+: in too few samples to decide for the batch'
    )
    const listed = record({ species: { kind: 'listed', formula: 'C8H24O4Si4' } })
    expect(knownSpeciesSummary(listed)).toBe('C8H24O4Si4: present across the batch')
    expect(knownSpeciesSummary(null)).toBe('')
  })

  it('says what the claim wrote and which samples it kept, and why', () => {
    const text = knownSpeciesTooltip(
      record({ files_by_reason: { hand_assigned: 1, carries_lines: 2 } })
    )
    expect(text).toBe(
      '[(CH4N2O)3+H]+: present across the batch (a source ion). ' +
        'Its peak is in 5 samples of 6; 1 sample read it as the species on their own. ' +
        'Claimed in 4 more samples. ' +
        'Kept as they read: 1 sample assigned by hand, 2 samples a reading with isotope lines of its own.'
    )
  })

  it('says why a present species was not claimed', () => {
    expect(knownSpeciesTooltip(record({ held: 'vote_holds', members_rewritten: 0 }))).toMatch(
      /Not claimed: the samples it could claim would not turn the batch peak's reading\.$/
    )
  })

  it('says each sample keeps its own claim where the batch did not decide it', () => {
    const text = knownSpeciesTooltip(
      record({
        verdict: 'unpooled',
        present: 1,
        files_by_reason: { own_claim: 1 },
        species: { kind: 'listed', formula: 'C8H24O4Si4' }
      })
    )
    expect(text).toMatch(/\(listed as background\)\./)
    expect(text).toMatch(/Each sample keeps its own reading: 1 sample its own claim\.$/)
    expect(text).not.toMatch(/Claimed in/)
  })

  it('is empty for no record', () => {
    expect(knownSpeciesTooltip(undefined)).toBe('')
  })
})
