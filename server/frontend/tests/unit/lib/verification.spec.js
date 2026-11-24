import { describe, it, expect } from 'vitest'

import {
  EVIDENCE_DETAILS,
  acquisitionRange,
  claimCandidates,
  claimOf,
  claimStatement,
  fromLocalInput,
  isClassClaim,
  levelLabel,
  needsStructure,
  toLocalInput,
  windowText
} from '@/lib/verification'

const GLUCOSE = 'WQZGKKKJIJFFOK-GASJEMHNSA-N'
const FRUCTOSE = 'RFSUNEUAIZKAJO-ARQDHWQXSA-N'

const label = (...args) => claimStatement(...args)?.label ?? null

describe('claimStatement', () => {
  it('reads the level the server fixes, under mapping version 2', () => {
    expect(label('reference_standard', null, 'msms')).toBe('1')
    expect(label('reference_standard')).toBe('2b')
    expect(label('msms', 'library')).toBe('2a')
    expect(label('msms', 'fragments')).toBe('2b')
    expect(label('msms', 'loss')).toBe('3b')
    expect(label('orthogonal', 'separation')).toBe('2b')
    expect(label('orthogonal', 'data_and_list')).toBe('3a')
    expect(label('orthogonal', 'class')).toBe('3b')
    expect(label('orthogonal', 'context_list')).toBe('3c')
    expect(label('orthogonal', 'homologue')).toBe('3d')
    expect(label('pattern', 'split_pinned')).toBe('4a')
    expect(label('pattern', 'split_open')).toBe('4b')
  })

  it('reads a pattern that says nothing of the split as open', () => {
    expect(label('pattern')).toBe('4b')
  })

  it('gives a standard with no independent line its lift', () => {
    expect(claimStatement('reference_standard').lift).toBe('an independent line')
    expect(claimStatement('reference_standard', null, 'separation').lift).toBeNull()
  })

  it('states none on a visual look, or before MS/MS says what it showed', () => {
    expect(claimStatement('visual')).toBeNull()
    expect(claimStatement(null)).toBeNull()
    expect(claimStatement('msms')).toBeNull()
    expect(claimStatement('orthogonal', 'split_open')).toBeNull()
  })

  it('mirrors the server: every detail the vocabulary offers has a level', () => {
    for (const [evidence, details] of Object.entries(EVIDENCE_DETAILS)) {
      for (const detail of details) expect(label(evidence, detail.value)).toMatch(/^[1-4][a-d]?$/)
    }
  })

  it('labels a level with its letter, a version-1 level without, and nothing for none', () => {
    expect(levelLabel(2, 'b')).toBe('Level 2b')
    expect(levelLabel(2)).toBe('Level 2')
    expect(levelLabel(1, null)).toBe('Level 1')
    expect(levelLabel(null)).toBeNull()
  })
})

describe('needsStructure and isClassClaim', () => {
  it('is the statements about a structure', () => {
    expect(needsStructure('reference_standard')).toBe(true)
    expect(needsStructure('msms', 'library')).toBe(true)
    expect(needsStructure('orthogonal', 'context_list')).toBe(true)
    expect(needsStructure('msms', 'loss')).toBe(false)
    expect(['pattern', 'visual', null].some((evidence) => needsStructure(evidence))).toBe(false)
  })

  it('is a class claim on one loss or orthogonal class evidence', () => {
    expect(isClassClaim('msms', 'loss')).toBe(true)
    expect(isClassClaim('orthogonal', 'class')).toBe(true)
    expect(isClassClaim('msms', 'fragments')).toBe(false)
    expect(isClassClaim('pattern')).toBe(false)
  })
})

describe('a recorded claim', () => {
  it('reads the statement its context keeps', () => {
    const claim = { evidence_detail: 'loss', independent_line: null, evidence: 'x', lift: 'y' }
    expect(claimOf({ context: { level_mapping_version: 2, claim } })).toBe(claim)
    expect(claimOf({ context: { level_mapping_version: 1 } })).toBeNull()
    expect(claimOf(null)).toBeNull()
  })

  it('says a window, or nothing without one', () => {
    expect(windowText({ window_start_utc: null })).toBeNull()
    const text = windowText({
      window_start_utc: '2026-11-01T08:00:00Z',
      window_end_utc: '2026-11-08T08:00:00Z'
    })
    expect(text).toMatch(/^from .+ to .+$/)
  })
})

describe('the window inputs', () => {
  it('round-trips a time through the local input, to the second', () => {
    const iso = '2026-11-01T08:00:30.000Z'
    expect(fromLocalInput(toLocalInput(iso))).toBe(iso)
  })

  it('takes the last second of a window whole', () => {
    expect(fromLocalInput(toLocalInput('2026-11-01T08:00:30.000Z'), { end: true })).toBe(
      '2026-11-01T08:00:30.999Z'
    )
  })

  it('reads nothing from nothing', () => {
    expect(toLocalInput(null)).toBe('')
    expect(toLocalInput('not a time')).toBe('')
    expect(fromLocalInput('')).toBeNull()
  })

  it('starts from the batch samples first and last acquisition', () => {
    expect(
      acquisitionRange([
        { datetime_utc: '2026-11-03T08:00:00Z' },
        { datetime_utc: '2026-11-01T08:00:00Z' },
        { datetime_utc: null },
        { datetime_utc: '2026-11-02T08:00:00Z' }
      ])
    ).toEqual({ first: '2026-11-01T08:00:00Z', last: '2026-11-03T08:00:00Z' })
    expect(acquisitionRange([])).toBeNull()
    expect(acquisitionRange(undefined)).toBeNull()
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
