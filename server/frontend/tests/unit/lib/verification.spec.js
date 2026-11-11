import { describe, it, expect } from 'vitest'

import { claimCandidates, claimLevel, levelLabel, needsStructure } from '@/lib/verification'

const GLUCOSE = 'WQZGKKKJIJFFOK-GASJEMHNSA-N'
const FRUCTOSE = 'RFSUNEUAIZKAJO-ARQDHWQXSA-N'

describe('claimLevel', () => {
  it('reads the level the server fixes from the evidence', () => {
    expect(claimLevel('reference_standard')).toBe(1)
    expect(claimLevel('msms')).toBe(2)
    expect(claimLevel('orthogonal')).toBe(3)
    expect(claimLevel('pattern')).toBe(4)
  })

  it('states none on a visual look, or with no evidence', () => {
    expect(claimLevel('visual')).toBeNull()
    expect(claimLevel(null)).toBeNull()
  })

  it('labels a level, and nothing for none', () => {
    expect(levelLabel(2)).toBe('Level 2')
    expect(levelLabel(null)).toBeNull()
  })
})

describe('needsStructure', () => {
  it('is the evidence that is a statement about a structure', () => {
    expect(['reference_standard', 'msms', 'orthogonal'].every(needsStructure)).toBe(true)
    expect(['pattern', 'visual', null].some(needsStructure)).toBe(false)
  })
})

describe('claimCandidates', () => {
  it('puts the target first, then the reference identities', () => {
    const candidates = claimCandidates({
      target: { name: 'Glucose', inchikey: GLUCOSE },
      identities: [{ name: 'D-fructose', source: 'sugars', inchikey: FRUCTOSE }]
    })
    expect(candidates).toEqual([
      { inchikey: GLUCOSE, name: 'Glucose', label: 'target' },
      { inchikey: FRUCTOSE, name: 'D-fructose', label: 'sugars' }
    ])
  })

  it('offers each InChIKey once, the first label keeping it', () => {
    const candidates = claimCandidates({
      target: { name: 'Glucose', inchikey: GLUCOSE },
      identities: [{ name: 'D-glucose', source: 'sugars', inchikey: ` ${GLUCOSE}` }]
    })
    expect(candidates).toEqual([{ inchikey: GLUCOSE, name: 'Glucose', label: 'target' }])
  })

  it('leaves out an identity that names no structure', () => {
    const candidates = claimCandidates({
      identities: [
        { name: 'unkeyed', source: 'sugars', inchikey: null },
        { name: null, inchikey: FRUCTOSE }
      ]
    })
    expect(candidates).toEqual([{ inchikey: FRUCTOSE, name: null, label: 'reference list' }])
  })

  it('leaves out a key that is not an InChIKey, which the server would refuse', () => {
    const candidates = claimCandidates({
      target: { name: 'bare block', inchikey: 'WQZGKKKJIJFFOK' },
      identities: [
        { name: 'placeholder', source: 'custom', inchikey: 'N/A' },
        { name: 'lowercase', source: 'custom', inchikey: GLUCOSE.toLowerCase() },
        { name: 'D-fructose', source: 'sugars', inchikey: FRUCTOSE }
      ]
    })
    expect(candidates).toEqual([{ inchikey: FRUCTOSE, name: 'D-fructose', label: 'sugars' }])
  })

  it('has none for a row no list names', () => {
    expect(claimCandidates()).toEqual([])
    expect(claimCandidates({ identities: null })).toEqual([])
  })
})
