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
    expect(text).toMatch(/Each sample keeps its own reading: 1 sample claimed it\.$/)
    expect(text).not.toMatch(/Claimed in/)
  })

  it('says which samples an earlier batch decision claimed against the verdict', () => {
    const text = knownSpeciesTooltip(
      record({ verdict: 'absent', test_held: 0, files_by_reason: { batch_claim: 4 } })
    )
    expect(text).toMatch(/0 samples read it as the species on their own\./)
    expect(text).toMatch(/Still claimed on an earlier batch decision: 4 samples\.$/)
    expect(text).not.toMatch(/its own reading/)
  })

  it('says apart the samples on their own claim and on an earlier decision', () => {
    const text = knownSpeciesTooltip(
      record({ verdict: 'absent', files_by_reason: { batch_claim: 2, own_claim: 1 } })
    )
    expect(text).toMatch(
      /Each sample keeps its own reading: 1 sample claimed it\. Still claimed on an earlier batch decision: 2 samples\.$/
    )
  })

  it('names a twin batch peak that claims the species, and samples it reads in', () => {
    const text = knownSpeciesTooltip(
      record({ held: 'twin', twin_of_mz: 193.1044, members_rewritten: 0, files_by_reason: {} })
    )
    expect(text).toMatch(
      /Not claimed: another batch peak of its trace, at m\/z 193\.1044, claims it\.$/
    )
    // A record without the m/z still says another batch peak claims.
    expect(
      knownSpeciesTooltip(record({ held: 'twin', members_rewritten: 0, files_by_reason: {} }))
    ).toMatch(/Not claimed: another batch peak of its trace claims it\.$/)
    expect(knownSpeciesTooltip(record({ files_by_reason: { reads_on_twin: 2 } }))).toMatch(
      /Kept as they read: 2 samples reading it on another batch peak of its trace\.$/
    )
  })

  it('is empty for no record', () => {
    expect(knownSpeciesTooltip(undefined)).toBe('')
  })
})
