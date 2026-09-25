import { describe, expect, it } from 'vitest'

import {
  mechanismPolarity,
  mechanismProblem,
  mechanismTerms,
  parseMechanism,
  standardMechanism
} from '@/lib/mechanism'

// Every legacy spelling the fleet's servers store, with its standard one - the
// same table the library's test_mechanism_notation.py pins, so the two agree.
const STORED = [
  ['+', '[M]+.'],
  ['-', '[M]-.'],
  ['+H+', '[M+H]+'],
  ['-H+', '[M-H]-'],
  ['+Br-', '[M+Br]-'],
  ['+Br2-', '[M+Br2]-'],
  ['+Br3-', '[M+Br3]-'],
  ['+I-', '[M+I]-'],
  ['+I2-', '[M+I2]-'],
  ['+I3-', '[M+I3]-'],
  ['+NO3-', '[M+NO3]-'],
  ['+^NO3-', '[M+^NO3]-'],
  ['+CO3-', '[M+CO3]-'],
  ['+HSO4-', '[M+HSO4]-'],
  ['+(HNO3)NO3-', '[M+HNO3+NO3]-'],
  ['+NH4+', '[M+NH4]+'],
  ['+^NH4+', '[M+^NH4]+'],
  ['+Na+', '[M+Na]+'],
  ['+C4H11N+', '[M+C4H11N]+'],
  ['+(CH4N2O)H+', '[M+CH4N2O+H]+'],
  ['+(CH4N2O)2H+', '[M+(CH4N2O)2H]+'],
  ['+(C3H6O)H+', '[M+C3H6O+H]+'],
  ['+(C6H10O2)H+', '[M+C6H10O2+H]+'],
  ['+(C6H15N)H+', '[M+C6H15N+H]+'],
  ['-H-', '[M-H]+'],
  ['-CH3-', '[M-CH3]+'],
  ['+[15N]O3-', '[M+[15N]O3]-'],
  ['+((CH3CH2)2NH)H+', '[M+(CH3CH2)2NH+H]+'],
  ['+(A)(B)+', '[M+A+(B)]+'],
  ['+(CH4N2O)+', '[M+(CH4N2O)]+']
]

describe('standardMechanism', () => {
  it.each(STORED)('writes %s as %s', (legacy, standard) => {
    expect(standardMechanism(legacy)).toBe(standard)
    expect(standardMechanism(standard)).toBe(standard)
  })

  it('writes a standard spelling as the server stores it', () => {
    expect(standardMechanism('[M+(CH4N2O)H]+')).toBe('[M+CH4N2O+H]+')
    expect(standardMechanism('  [M+H]+ ')).toBe('[M+H]+')
  })

  it('shows text that reads as neither notation as it is', () => {
    // A stored row the rules refuse, and a fixture's ion formula.
    expect(standardMechanism('+H+ (legacy 0123)')).toBe('+H+ (legacy 0123)')
    expect(standardMechanism('H3O+')).toBe('H3O+')
    expect(standardMechanism(null)).toBe('')
    expect(standardMechanism(undefined)).toBe('')
  })
})

describe('parseMechanism', () => {
  it.each([
    ['[M+H]+', true, 'H', 1],
    ['[M-H]-', false, 'H', -1],
    ['[M-H]+', false, 'H', 1],
    ['[M+Br]-', true, 'Br', -1],
    ['[M+CH4N2O+H]+', true, '(CH4N2O)H', 1],
    ['[M]+.', false, '', 1],
    ['[M]-.', true, '', -1]
  ])('reads %s with the ion charge last', (notation, addition, moiety, charge) => {
    expect(parseMechanism(notation)).toEqual({ addition, moiety, charge })
  })

  it.each(STORED)('reads %s as the same mechanism as %s', (legacy, standard) => {
    expect(parseMechanism(legacy)).toEqual(parseMechanism(standard))
  })
})

describe('mechanismProblem', () => {
  it.each(['[M+H]+', '+H+', '[M]-.', '-', '[M+^NO3]-', '[M+CH4N2O+H]+'])(
    'accepts %s',
    (notation) => {
      expect(mechanismProblem(notation)).toBeNull()
    }
  )

  it.each([
    ['[M]+', 'Electron transfer'],
    ['[M+H]+.', 'dot'],
    ['[2M+H]+', 'one molecule'],
    ['[M+2H]2+', 'singly charged'],
    ['[M+Na-2H]-', 'not both'],
    ['[M+2H2O+H]+', 'formula'],
    ['[M+(H+H]+', 'unbalanced'],
    ['H+', "'[M+H]+'"],
    ['', "'[M+H]+'"]
  ])('says why %s does not read', (notation, reason) => {
    expect(mechanismProblem(notation)).toContain(reason)
  })
})

describe('mechanismTerms', () => {
  it('lists what is added or removed, as the standard notation writes it', () => {
    expect(mechanismTerms('[M+CH4N2O+H]+')).toEqual(['CH4N2O', 'H'])
    expect(mechanismTerms('+(CH4N2O)H+')).toEqual(['CH4N2O', 'H'])
    expect(mechanismTerms('[M+(CH4N2O)2+H]+')).toEqual(['(CH4N2O)2', 'H'])
    expect(mechanismTerms('-H+')).toEqual(['H'])
    expect(mechanismTerms('[M]+.')).toEqual([])
  })
})

describe('mechanismPolarity', () => {
  it('is the charge of the ion, whichever notation', () => {
    expect(mechanismPolarity('[M-H]-')).toBe('-')
    expect(mechanismPolarity('-H+')).toBe('-')
    expect(mechanismPolarity('-H-')).toBe('+')
    expect(mechanismPolarity('[M]+.')).toBe('+')
    expect(mechanismPolarity('nonsense')).toBeNull()
  })
})
