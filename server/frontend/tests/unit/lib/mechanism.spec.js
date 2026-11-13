import { describe, expect, it } from 'vitest'

import {
  describeMechanism,
  mechanismFormulas,
  mechanismPolarity,
  mechanismProblem,
  mechanismRow,
  mechanismTerms,
  parseMechanism,
  standardMechanism
} from '@/lib/mechanism'

// Every legacy spelling the fleet's servers store, with its standard one, and
// the unusual ones the map must still carry both ways - the same tables the
// library's test_mechanism_notation.py pins, so the two agree.
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
  ['+(H)(H2O)H2O+', '[M+H+H2O+H2O]+'],
  ['+(H2O)2H+', '[M+(H2O)2H]+'],
  ['+(CH3)3C+', '[M+(CH3)3C]+'],
  ['+((A))(B)+', '[M+(A)+(B)]+'],
  ['+(CH4N2O)+', '[M+(CH4N2O)]+']
]

// A mechanism typed with its terms in another order, in either notation, and
// the one spelling it is stored and shown in.
const OUT_OF_ORDER = [
  ['[M+H+CH4N2O]+', '[M+CH4N2O+H]+'],
  ['+(H)CH4N2O+', '[M+CH4N2O+H]+'],
  ['[M+NO3+HNO3]-', '[M+HNO3+NO3]-'],
  ['[M+H2O+H]+', '[M+H+H2O]+'],
  ['+(H2O)(H2O)H+', '[M+H+H2O+H2O]+'],
  ['+(A)(B)+', '[M+(B)+A]+'],
  // A term that opens with a group reads as the terms it holds, wherever it
  // stands among the others.
  ['[M+(B)C+(A)]+', '[M+(A)+B+C]+'],
  ['[M+(H2O)Na+K]+', '[M+H2O+K+Na]+'],
  ['[M+K+(H2O)Na]+', '[M+H2O+K+Na]+'],
  ['+(K)(H2O)Na+', '[M+H2O+K+Na]+'],
  ['+((H2O)Na)K+', '[M+H2O+K+Na]+'],
  ['[M+(H2O)H+NH4]+', '[M+H+H2O+NH4]+'],
  ['[M+NH4+(H2O)H]+', '[M+H+H2O+NH4]+']
]

// The shapes only the standard notation says, as typed and as stored, and the
// row the server stores - the library's NEW_SHAPES.
const NEW_SHAPES = [
  ['[2M+H]+', '[2M+H]+', ['add', 'H', 1, 2, null]],
  ['[3M+Na]+', '[3M+Na]+', ['add', 'Na', 1, 3, null]],
  ['[2M-H]-', '[2M-H]-', ['remove', 'H', 1, 2, null]],
  ['[2M+Br]-', '[2M+Br]-', ['add', 'Br', -1, 2, null]],
  ['[2M]+.', '[2M]+.', ['remove', '', -1, 2, null]],
  ['[M+2H]2+', '[M+2H]2+', ['add', '(H)2', 2, 1, null]],
  ['[M+(H)2]2+', '[M+2H]2+', ['add', '(H)2', 2, 1, null]],
  ['[M+3H]3+', '[M+3H]3+', ['add', '(H)3', 3, 1, null]],
  ['[M-2H]2-', '[M-2H]2-', ['remove', '(H)2', 2, 1, null]],
  ['[M+2NH4]2+', '[M+2NH4]2+', ['add', '(NH4)2', 2, 1, null]],
  ['[M+Ca]2+', '[M+Ca]2+', ['add', 'Ca', 2, 1, null]],
  ['[M+H+Na]2+', '[M+H+Na]2+', ['add', '(H)Na', 2, 1, null]],
  ['[M+Na+H]2+', '[M+H+Na]2+', ['add', '(H)Na', 2, 1, null]],
  // One species as many times as the charge is the coefficient form.
  ['[M+H+H]2+', '[M+2H]2+', ['add', '(H)2', 2, 1, null]],
  ['[M+(H)H]2+', '[M+2H]2+', ['add', '(H)2', 2, 1, null]],
  ['[M-H-H]2-', '[M-2H]2-', ['remove', '(H)2', 2, 1, null]],
  ['[M+H+H+H]3+', '[M+3H]3+', ['add', '(H)3', 3, 1, null]],
  ['[M+K+K]2+', '[M+2K]2+', ['add', '(K)2', 2, 1, null]],
  ['[2M+H+H]2+', '[2M+2H]2+', ['add', '(H)2', 2, 2, null]],
  ['[M+H+H-H2O]2+', '[M+2H-H2O]2+', ['add', '(H)2', 2, 1, 'H2O']],
  // Several species stay one term each, as the coefficient's hint says.
  ['[M+H+H+Na]3+', '[M+H+H+Na]3+', ['add', '(H)(H)Na', 3, 1, null]],
  // Multivalent ions carry the whole charge.
  ['[M+Mg]2+', '[M+Mg]2+', ['add', 'Mg', 2, 1, null]],
  ['[M+Fe]3+', '[M+Fe]3+', ['add', 'Fe', 3, 1, null]],
  ['[M+Hg2]2+', '[M+Hg2]2+', ['add', 'Hg2', 2, 1, null]],
  // Fewer repeats than the charge stay as typed: each Ca carries two.
  ['[M+Ca+Ca]4+', '[M+Ca+Ca]4+', ['add', '(Ca)Ca', 4, 1, null]],
  // A count in the formula other than the charge is no coefficient written into it.
  ['[M+H3]2+', '[M+H3]2+', ['add', 'H3', 2, 1, null]],
  // A group multiplied by anything but the ion's charge is no coefficient.
  ['[M+(H)3]2+', '[M+(H)3]2+', ['add', '(H)3', 2, 1, null]],
  ['[M+H-H2O]+', '[M+H-H2O]+', ['add', 'H', 1, 1, 'H2O']],
  ['[M-H2O+H]+', '[M+H-H2O]+', ['add', 'H', 1, 1, 'H2O']],
  ['[M+Na-CO2]+', '[M+Na-CO2]+', ['add', 'Na', 1, 1, 'CO2']],
  ['[M+H-NH3-H2O]+', '[M+H-H2O-NH3]+', ['add', 'H', 1, 1, '(H2O)NH3']],
  ['[M+H-(H2O)2]+', '[M+H-(H2O)2]+', ['add', 'H', 1, 1, '(H2O)2']],
  // Dihydrogen is a neutral; a removed proton is not.
  ['[M+H-H2]+', '[M+H-H2]+', ['add', 'H', 1, 1, 'H2']],
  ['[2M+2H-H2O]2+', '[2M+2H-H2O]2+', ['add', '(H)2', 2, 2, 'H2O']]
]

// Terms to write mechanisms of in every order, the grouped and the multiplied
// among them - the library's test pins the same set.
const TERMS = ['H', 'K', 'H2O', '(H2O)Na', '(H2O)2', '(CH3)3C', '((A)B)C', '(A)', '^NO3']

const combinations = (items, count) =>
  count === 0
    ? [[]]
    : items.flatMap((item, i) =>
        combinations(items.slice(i + 1), count - 1).map((rest) => [item, ...rest])
      )

const permutations = (items) =>
  items.length <= 1
    ? [items]
    : items.flatMap((item, i) =>
        permutations([...items.slice(0, i), ...items.slice(i + 1)]).map((rest) => [item, ...rest])
      )

describe('standardMechanism', () => {
  it.each(STORED)('writes %s as %s', (legacy, standard) => {
    expect(standardMechanism(legacy)).toBe(standard)
    expect(standardMechanism(standard)).toBe(standard)
  })

  it.each(OUT_OF_ORDER)('writes %s, its terms in order, as %s', (typed, stored) => {
    expect(standardMechanism(typed)).toBe(stored)
    expect(parseMechanism(typed)).toEqual(parseMechanism(stored))
  })

  it.each([...combinations(TERMS, 2), ...combinations(TERMS, 3)].map((terms) => [terms]))(
    'writes every order of %j as one spelling',
    (terms) => {
      const written = new Set(
        permutations(terms).map((order) =>
          standardMechanism(`[M${order.map((term) => `+${term}`).join('')}]+`)
        )
      )
      expect(written.size).toBe(1)
      const [stored] = written
      expect(standardMechanism(stored)).toBe(stored)
    }
  )

  it.each(NEW_SHAPES)(
    'writes %s, which only the standard notation says, as %s',
    (typed, stored) => {
      expect(standardMechanism(typed)).toBe(stored)
      expect(standardMechanism(stored)).toBe(stored)
      expect(parseMechanism(typed)).toEqual(parseMechanism(stored))
    }
  )

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
    expect(parseMechanism(notation)).toEqual({
      addition,
      moiety,
      charge,
      multimer: 1,
      neutralLoss: ''
    })
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
    ['[1M+H]+', 'One molecule'],
    ['[M+H]1+', 'single charge'],
    ['[M+H]', "the ion's charge"],
    ['[M]2+.', 'one electron'],
    ['[M+2H]+', 'formula'],
    ['[M+2H+Na]3+', 'only term'],
    ['[M+3H]2+', "the ion's charge"],
    ['[M+Na-2H]-', 'neutral loss'],
    ['[M+H-2H2O]+', 'neutral loss'],
    ['[M+2H2O+H]+', 'formula'],
    ['[M+(H+H]+', 'unbalanced'],
    ['[M+A)(B]+', 'unbalanced'],
    ['H+', "'[M+H]+'"],
    ['', "'[M+H]+'"]
  ])('says why %s does not read', (notation, reason) => {
    expect(mechanismProblem(notation)).toContain(reason)
  })

  it.each([
    // The coefficient left out: one species of a single charge carrying the
    // ion's whole charge.
    ['[M+H]2+', '[M+2H]2+'],
    ['[M-H]2-', '[M-2H]2-'],
    ['[M+Na]3+', '[M+3Na]3+'],
    ['[M+NH4]2+', '[M+2NH4]2+'],
    ['[M+NO3]2-', '[M+2NO3]2-'],
    ['[M+Ag]2+', '[M+2Ag]2+'],
    ['[M+Cl]2-', '[M+2Cl]2-'],
    ['[2M+H-H2O]2+', '[2M+2H-H2O]2+'],
    // The coefficient written into the formula.
    ['[M+H2]2+', '[M+2H]2+'],
    ['[M-H2]2-', '[M-2H]2-'],
    ['[M+Na2]2+', '[M+2Na]2+'],
    ['[M+H3]3+', '[M+3H]3+'],
    ['[M+Br2]2-', '[M+2Br]2-']
  ])('refuses %s, a species of one charge carrying more, for %s', (notation, meant) => {
    const problem = mechanismProblem(notation)
    expect(problem).toContain('one charge')
    expect(problem).toContain(`write '${meant}'`)
    expect(mechanismProblem(meant)).toBeNull()
  })

  // The library's test pins the same table.
  it.each('H Li Na K Rb Cs F Cl Br I NH4 NO3 Ag'.split(' '))(
    'refuses one %s carrying two charges',
    (species) => {
      expect(mechanismProblem(`[M+${species}]2+`)).toContain('one charge')
      expect(mechanismProblem(`[M+2${species}]2+`)).toBeNull()
    }
  )

  it.each(['[M+H2O-H]-', '[M+Na-H-H]-', '[M+Na+Na-H]+', '[M+Na-(H)H]-', '[M+NH4-H-H2O]+'])(
    'refuses %s: a removed proton is never a neutral loss',
    (notation) => {
      expect(mechanismProblem(notation)).toContain("'H' is a proton")
    }
  )

  it('keeps a coefficient on the only term', () => {
    expect(mechanismProblem('[M+2H+2H]2+')).toContain('only term')
  })
})

describe('mechanismTerms', () => {
  it('lists what is added or removed, as the standard notation writes it', () => {
    expect(mechanismTerms('[M+CH4N2O+H]+')).toEqual(['CH4N2O', 'H'])
    expect(mechanismTerms('+(CH4N2O)H+')).toEqual(['CH4N2O', 'H'])
    expect(mechanismTerms('[M+(CH4N2O)2+H]+')).toEqual(['(CH4N2O)2', 'H'])
    expect(mechanismTerms('-H+')).toEqual(['H'])
    expect(mechanismTerms('[M]+.')).toEqual([])
    expect(mechanismTerms('[M+2H]2+')).toEqual(['2H'])
    expect(mechanismTerms('[M+H-H2O]+')).toEqual(['H'])
  })
})

describe('mechanismFormulas', () => {
  it('lists every formula, the neutral loss and a coefficient as its group', () => {
    expect(mechanismFormulas('[M+CH4N2O+H]+')).toEqual(['CH4N2O', 'H'])
    expect(mechanismFormulas('[M+2H]2+')).toEqual(['(H)2'])
    expect(mechanismFormulas('[M+H-H2O-NH3]+')).toEqual(['H', 'H2O', 'NH3'])
    expect(mechanismFormulas('[M]+.')).toEqual([])
  })
})

describe('mechanismRow', () => {
  it.each(NEW_SHAPES)('reads %s as the row the server stores', (typed, _stored, row) => {
    const [operation, species, speciesCharge, multimerN, neutralLoss] = row
    expect(mechanismRow(typed)).toMatchObject({
      operation,
      species,
      speciesCharge,
      multimerN,
      neutralLoss
    })
  })

  it.each([
    ['[M+H]+', 'add', 'H', 1, 1],
    ['-H+', 'remove', 'H', 1, -1],
    ['[M-H]+', 'remove', 'H', -1, 1],
    ['[M+Br]-', 'add', 'Br', -1, -1],
    ['[M]+.', 'remove', '', -1, 1],
    ['[M]-.', 'add', '', -1, -1],
    ['[M-2H]2-', 'remove', '(H)2', 2, -2],
    // Only a mechanism that adds has a neutral loss: these remove all they name.
    ['[M-H-H2O]-', 'remove', '(H)H2O', 1, -1],
    ['[M-CO2-H]-', 'remove', '(CO2)H', 1, -1]
  ])('derives the ion of %s from its row', (notation, operation, species, speciesCharge, ion) => {
    expect(mechanismRow(notation)).toEqual({
      operation,
      species,
      speciesCharge,
      multimerN: 1,
      neutralLoss: null,
      ionCharge: ion,
      polarity: ion > 0 ? '+' : '-'
    })
  })

  it('is null for what does not read', () => {
    expect(mechanismRow('nonsense')).toBeNull()
  })
})

describe('describeMechanism', () => {
  it.each([
    ['[M+H]+', 'Ion charge +'],
    ['-H+', 'Ion charge -'],
    ['[M+2H]2+', 'Ion charge 2+'],
    ['[2M+H]+', 'Ion charge + · 2 molecules'],
    ['[M+H-H2O-NH3]+', 'Ion charge + · loses H2O and NH3'],
    ['[2M+2H-H2O]2+', 'Ion charge 2+ · 2 molecules · loses H2O'],
    ['nonsense', '']
  ])('says what ion %s makes', (notation, words) => {
    expect(describeMechanism(notation)).toBe(words)
  })
})

describe('mechanismPolarity', () => {
  it('is the charge of the ion, whichever notation', () => {
    expect(mechanismPolarity('[M-H]-')).toBe('-')
    expect(mechanismPolarity('-H+')).toBe('-')
    expect(mechanismPolarity('-H-')).toBe('+')
    expect(mechanismPolarity('[M]+.')).toBe('+')
    expect(mechanismPolarity('[M-2H]2-')).toBe('-')
    expect(mechanismPolarity('nonsense')).toBeNull()
  })
})
