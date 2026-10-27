import { describe, it, expect } from 'vitest'

import {
  compoundIdentity,
  identityChanges,
  isValidInchi,
  isValidInchikey,
  isValidChemicalFormula,
  isSameCompound,
  isIsomer,
  inchikeyOf,
  findExistingCompound,
  formatIsotopeFormula,
  formatIsotopeLabel,
  isMonoisotopicFormula,
  labelledIsotopes,
  monoisotopicIsotope,
  neutralKey,
  parseCompoundPaste,
  validateCompoundPaste
} from '@/lib/chem'

describe('isValidChemicalFormula', () => {
  it.each(['C6H12O6', 'Ca(OH)2', '(NH4)2SO4', '()', 'H^NO3', 'H2SO4', 'C'])(
    'accepts %s',
    (formula) => {
      expect(isValidChemicalFormula(formula)).toBe(true)
    }
  )

  it.each(['invalid123', 'h2o', '123', 'C6H12O6-', '', null, undefined])(
    'rejects %s',
    (formula) => {
      expect(isValidChemicalFormula(formula)).toBe(false)
    }
  )

  it('normalizes surrounding whitespace before validating', () => {
    expect(isValidChemicalFormula('  C6H12O6  ')).toBe(true)
  })
})

describe('isSameCompound', () => {
  const glucose = { target_compound_formula: 'C6H12O6', cas_number: '50-99-7' }

  it('matches by normalized CAS number', () => {
    const other = { target_compound_formula: 'C2H6O', cas_number: ' 50-99-7 ' }
    expect(isSameCompound(glucose, other)).toBe(true)
  })

  it('matches by case-insensitive formula', () => {
    const other = { target_compound_formula: ' c6h12o6 ' }
    expect(isSameCompound(glucose, other)).toBe(true)
  })

  it('does not match different compounds', () => {
    const other = { target_compound_formula: 'C2H6O', cas_number: '64-17-5' }
    expect(isSameCompound(glucose, other)).toBe(false)
  })

  it('never matches when a formula is missing or blank', () => {
    expect(isSameCompound(glucose, { cas_number: '50-99-7' })).toBe(false)
    expect(isSameCompound(glucose, { target_compound_formula: '  ' })).toBe(false)
    expect(isSameCompound(glucose, null)).toBe(false)
  })
})

const ETHANOL = 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N'
const DIMETHYL_ETHER = 'LCGLNKUTAGEVQW-UHFFFAOYSA-N'

describe('isSameCompound by structure', () => {
  const ethanol = { target_compound_formula: 'C2H6O', inchikey: ETHANOL }

  it('tells isomers apart by their InChIKeys', () => {
    expect(
      isSameCompound(ethanol, { target_compound_formula: 'C2H6O', inchikey: DIMETHYL_ETHER })
    ).toBe(false)
  })

  it('matches one InChIKey however it is written', () => {
    const other = { target_compound_formula: 'C2H6O', inchikey: ` ${ETHANOL.toLowerCase()} ` }
    expect(isSameCompound(ethanol, other)).toBe(true)
  })

  it('matches by formula when either side has no InChIKey', () => {
    expect(isSameCompound(ethanol, { target_compound_formula: 'C2H6O' })).toBe(true)
    expect(isSameCompound({ target_compound_formula: 'C2H6O', inchikey: '  ' }, ethanol)).toBe(true)
  })

  it('still matches by CAS number when the InChIKeys differ', () => {
    expect(
      isSameCompound(
        { ...ethanol, cas_number: '64-17-5' },
        { target_compound_formula: 'C2H6O', cas_number: '64-17-5', inchikey: DIMETHYL_ETHER }
      )
    ).toBe(true)
  })
})

describe('isIsomer', () => {
  const ethanol = { target_compound_formula: 'C2H6O', inchikey: ETHANOL }

  it('is one formula with two InChIKeys', () => {
    expect(isIsomer(ethanol, { target_compound_formula: 'c2h6o', inchikey: DIMETHYL_ETHER })).toBe(
      true
    )
  })

  it('is not the same InChIKey, another formula, or a side without a key', () => {
    expect(isIsomer(ethanol, { ...ethanol })).toBe(false)
    expect(isIsomer(ethanol, { target_compound_formula: 'C3H8O', inchikey: DIMETHYL_ETHER })).toBe(
      false
    )
    expect(isIsomer(ethanol, { target_compound_formula: 'C2H6O' })).toBe(false)
    expect(isIsomer({ inchikey: ETHANOL }, { inchikey: DIMETHYL_ETHER })).toBe(false)
  })
})

describe('inchikeyOf', () => {
  it('trims and upper-cases, and has none for a blank or missing key', () => {
    expect(inchikeyOf({ inchikey: ` ${ETHANOL.toLowerCase()}` })).toBe(ETHANOL)
    expect(inchikeyOf({ inchikey: ' ' })).toBe(null)
    expect(inchikeyOf({})).toBe(null)
    expect(inchikeyOf(null)).toBe(null)
  })
})

describe('findExistingCompound', () => {
  const list = [
    { target_compound_formula: 'C6H12O6', cas_number: '50-99-7' },
    { target_compound_formula: 'C2H6O', cas_number: '64-17-5' }
  ]

  it('finds a compound by formula', () => {
    expect(findExistingCompound(list, { target_compound_formula: 'c2h6o' })).toBe(list[1])
  })

  it('returns null when the search compound has no formula', () => {
    expect(findExistingCompound(list, { cas_number: '50-99-7' })).toBe(null)
    expect(findExistingCompound(list, null)).toBe(null)
  })

  it('finds no compound of the formula whose InChIKey differs', () => {
    const stored = [{ target_compound_formula: 'C2H6O', inchikey: ETHANOL }]
    const search = { target_compound_formula: 'C2H6O', inchikey: DIMETHYL_ETHER }
    expect(findExistingCompound(stored, search)).toBeUndefined()
  })

  it('returns undefined when nothing matches', () => {
    expect(findExistingCompound(list, { target_compound_formula: 'CH4' })).toBeUndefined()
  })

  it('prefers the compound of the InChIKey to a keyless one of the formula before it', () => {
    const stored = [
      { target_compound_formula: 'C2H6O', target_compound_name: 'unknown' },
      { target_compound_formula: 'C2H6O', inchikey: ETHANOL }
    ]
    const search = { target_compound_formula: 'C2H6O', inchikey: ETHANOL.toLowerCase() }
    expect(findExistingCompound(stored, search)).toBe(stored[1])
  })

  it('prefers the compound of the CAS number to one of the InChIKey before it', () => {
    const stored = [
      { target_compound_formula: 'C2H6O', inchikey: ETHANOL },
      { target_compound_formula: 'C2H6O', cas_number: '64-17-5' }
    ]
    const search = { target_compound_formula: 'C2H6O', cas_number: '64-17-5', inchikey: ETHANOL }
    expect(findExistingCompound(stored, search)).toBe(stored[1])
  })

  it('falls back to the first formula match for a search without a key', () => {
    const stored = [
      { target_compound_formula: 'C2H6O', target_compound_name: 'unknown' },
      { target_compound_formula: 'C2H6O', inchikey: ETHANOL }
    ]
    expect(findExistingCompound(stored, { target_compound_formula: 'C2H6O' })).toBe(stored[0])
  })
})

describe('parseCompoundPaste', () => {
  it('maps a single column to formulas only', () => {
    expect(parseCompoundPaste('H2O\nC6H12O6')).toEqual([
      { target_compound_formula: 'H2O' },
      { target_compound_formula: 'C6H12O6' }
    ])
  })

  it('handles Windows line endings and a trailing newline', () => {
    expect(parseCompoundPaste('H2O\r\nCO2\r\n')).toEqual([
      { target_compound_formula: 'H2O' },
      { target_compound_formula: 'CO2' }
    ])
  })

  it('drops a header cell from a single-column paste', () => {
    expect(parseCompoundPaste('Formula\nH2O')).toEqual([{ target_compound_formula: 'H2O' }])
  })

  it('keeps a lone single cell as-is for validation to judge', () => {
    expect(parseCompoundPaste('Formula')).toEqual([{ target_compound_formula: 'Formula' }])
  })

  it('maps two columns to name and formula', () => {
    expect(parseCompoundPaste('Water\tH2O')).toEqual([
      { target_compound_name: 'Water', target_compound_formula: 'H2O' }
    ])
  })

  it('maps three columns to name, formula and CAS number', () => {
    expect(parseCompoundPaste('Water\tH2O\t7732-18-5')).toEqual([
      { target_compound_name: 'Water', target_compound_formula: 'H2O', cas_number: '7732-18-5' }
    ])
  })

  it('maps a fourth column to the InChIKey', () => {
    expect(parseCompoundPaste('Methane\tCH4\t74-82-8\tVNWKTOKETHGBQD-UHFFFAOYSA-N')).toEqual([
      {
        target_compound_name: 'Methane',
        target_compound_formula: 'CH4',
        cas_number: '74-82-8',
        inchikey: 'VNWKTOKETHGBQD-UHFFFAOYSA-N'
      }
    ])
  })

  it('maps six columns to name, formula, CAS number, InChIKey, InChI and SMILES', () => {
    expect(
      parseCompoundPaste(
        'Methane\tCH4\t74-82-8\tVNWKTOKETHGBQD-UHFFFAOYSA-N\tInChI=1S/CH4/h1H4\tC\n' +
          'Water\tH2O\t\t\t\tO'
      )
    ).toEqual([
      {
        target_compound_name: 'Methane',
        target_compound_formula: 'CH4',
        cas_number: '74-82-8',
        inchikey: 'VNWKTOKETHGBQD-UHFFFAOYSA-N',
        inchi: 'InChI=1S/CH4/h1H4',
        smiles: 'C'
      },
      {
        target_compound_name: 'Water',
        target_compound_formula: 'H2O',
        cas_number: '',
        inchikey: '',
        inchi: '',
        smiles: 'O'
      }
    ])
  })
})

describe('validateCompoundPaste', () => {
  it('rejects empty or missing data', () => {
    expect(validateCompoundPaste(null).valid).toBe(false)
    expect(validateCompoundPaste([]).valid).toBe(false)
    expect(validateCompoundPaste([null]).valid).toBe(false)
  })

  it('accepts a formula-only column', () => {
    const result = validateCompoundPaste([
      { target_compound_formula: 'H2O' },
      { target_compound_formula: 'C6H12O6' }
    ])
    expect(result.valid).toBe(true)
    expect(result.message).toBe('Pasted 2 compounds')
  })

  it('accepts a single pasted formula cell', () => {
    const result = validateCompoundPaste([{ target_compound_formula: 'H2O' }])
    expect(result.valid).toBe(true)
    expect(result.message).toBe('Pasted 1 compound')
  })

  it('rejects a single column that does not contain formulas', () => {
    const result = validateCompoundPaste([
      { target_compound_formula: 'Water' },
      { target_compound_formula: 'Glucose' }
    ])
    expect(result.valid).toBe(false)
    expect(result.message).toContain('not a valid chemical formula')
  })

  it('accepts name and formula columns without formula syntax checks', () => {
    const result = validateCompoundPaste([
      { target_compound_name: 'Water', target_compound_formula: 'H2O' }
    ])
    expect(result.valid).toBe(true)
  })

  it('rejects rows missing a formula', () => {
    const result = validateCompoundPaste([
      { target_compound_name: 'Water', target_compound_formula: 'H2O' },
      { target_compound_name: 'Glucose', target_compound_formula: '' }
    ])
    expect(result.valid).toBe(false)
    expect(result.message).toBe('Some rows are missing a formula, which is required')
  })

  it('accepts six columns, blank structure cells included', () => {
    const result = validateCompoundPaste(
      parseCompoundPaste(
        'Methane\tCH4\t74-82-8\tVNWKTOKETHGBQD-UHFFFAOYSA-N\tInChI=1S/CH4/h1H4\tC\n' +
          'Water\tH2O\t\t\t\t'
      )
    )
    expect(result.valid).toBe(true)
    expect(result.message).toBe('Pasted 2 compounds')
  })

  it('rejects more than six columns', () => {
    const result = validateCompoundPaste(parseCompoundPaste('a\tCH4\tc\td\te\tf\tg'))
    expect(result.valid).toBe(false)
    expect(result.message).toBe('You pasted 7 columns but 1 to 6 are expected')
  })

  it('names a malformed InChIKey', () => {
    const result = validateCompoundPaste(
      parseCompoundPaste(
        'Methane\tCH4\t\tVNWKTOKETHGBQD-UHFFFAOYSA-N\nWater\tH2O\t\tXLYOFNOQVPJJNP'
      )
    )
    expect(result.valid).toBe(false)
    expect(result.message).toContain("'XLYOFNOQVPJJNP' is not an InChIKey")
  })

  it('names a malformed InChI', () => {
    const result = validateCompoundPaste(parseCompoundPaste('Methane\tCH4\t\t\t1S/CH4/h1H4\tC'))
    expect(result.valid).toBe(false)
    expect(result.message).toContain("'1S/CH4/h1H4' is not an InChI")
  })
})

describe('formatIsotopeFormula', () => {
  // Cases documented in the function docstring.
  it.each([
    ['C3H6O3', 'M0'],
    ['[13C]C2H6O3', '[13C]'],
    ['[13C]C2[2H]H5O3', '[13C][2H]'],
    ['[13C]2CH6O3', '[13C]2'],
    ['[13C]C2H6O3/C3H6[18O]O2', '[13C]/[18O]']
  ])('formats %s as %s', (formula, expected) => {
    expect(formatIsotopeFormula(formula)).toBe(expected)
  })

  it('returns an empty string for empty input', () => {
    expect(formatIsotopeFormula('')).toBe('')
    expect(formatIsotopeFormula(null)).toBe('')
  })
})

// A reagent ion's lines are labelled by the envelope prediction, bare; spelled
// in brackets they read like an analyte's isotopologue beside them.
describe('formatIsotopeLabel', () => {
  // Cases documented in the function docstring.
  it.each([
    ['81Br', '[81Br]'],
    ['81Br2', '[81Br]2'],
    ['81Br+18O', '[81Br][18O]'],
    ['13C', '[13C]'],
    ['M+1', 'M+1'],
    ['M0', 'M0']
  ])('formats %s as %s', (label, expected) => {
    expect(formatIsotopeLabel(label)).toBe(expected)
  })

  it('returns an empty string for no label', () => {
    expect(formatIsotopeLabel('')).toBe('')
    expect(formatIsotopeLabel(null)).toBe('')
  })
})

// A labelled reagent's atom is bracketed in an isotopologue formula like any
// substituted isotope, so the brackets alone cannot say which line is a labelled
// ion's M0. The ion formula names the labels, as caret elements.
describe('labelledIsotopes', () => {
  it.each([
    ['C9H16O7^N-', { '[15N]': 1 }],
    ['HO6^N2-', { '[15N]': 2 }],
    // A nitrogen of the analyte's own is not a label.
    ['C2H3N^NO5-', { '[15N]': 1 }],
    ['CHBr4-', {}],
    ['C6H12O6', {}]
  ])('reads the labels of %s', (ionFormula, labels) => {
    expect(labelledIsotopes(ionFormula)).toEqual(labels)
  })

  it('reads no labels without an ion formula', () => {
    expect(labelledIsotopes(null)).toEqual({})
    expect(labelledIsotopes(undefined)).toEqual({})
  })
})

describe('isMonoisotopicFormula', () => {
  it('takes the line naming exactly the labels as a labelled ion M0', () => {
    const labels = labelledIsotopes('C9H16O7^N-')

    expect(isMonoisotopicFormula('[15N]C9H16O7-', labels)).toBe(true)
    // The reagent's unlabelled remainder, one mass unit below the M0.
    expect(isMonoisotopicFormula('C9H16NO7-', labels)).toBe(false)
    expect(isMonoisotopicFormula('[13C][15N]C8H16O7-', labels)).toBe(false)
  })

  it('needs every labelled atom of a two-label ion at its label', () => {
    const labels = labelledIsotopes('HO6^N2-')

    expect(isMonoisotopicFormula('[15N]2HO6-', labels)).toBe(true)
    expect(isMonoisotopicFormula('[15N]HNO6-', labels)).toBe(false)
    expect(isMonoisotopicFormula('HN2O6-', labels)).toBe(false)
  })

  it('takes the formula without a bracket as an unlabelled ion M0', () => {
    expect(isMonoisotopicFormula('CHBr4-')).toBe(true)
    expect(isMonoisotopicFormula('[81Br]CHBr3-', {})).toBe(false)
  })

  it('reads a merged low-resolution line as the M0 when one of its names is', () => {
    const labels = labelledIsotopes('C9H16O7^N-')

    expect(isMonoisotopicFormula('[15N]C9H16O7-/[13C]C8H16NO7-', labels)).toBe(true)
    expect(isMonoisotopicFormula('[13C][15N]C8H16O7-/[2H][15N]C9H15O7-', labels)).toBe(false)
  })

  // An imported run writes a labelled ion's M0 in the ion's own notation, caret
  // and all, so its isotopologue formula is the ion formula itself.
  it('takes a label written as the caret element as the labelled isotope', () => {
    const labels = labelledIsotopes('C10H18O7^N-')

    expect(isMonoisotopicFormula('C10H18O7^N-', labels)).toBe(true)
    expect(isMonoisotopicFormula('[13C]C9H18O7^N-', labels)).toBe(false)
    expect(isMonoisotopicFormula('HO6^N2-', labelledIsotopes('HO6^N2-'))).toBe(true)
  })

  it('is no M0 without a formula', () => {
    expect(isMonoisotopicFormula('', {})).toBe(false)
    expect(isMonoisotopicFormula(null, {})).toBe(false)
  })
})

// The labels an isotopologue table shows for a labelled ion. They count from the
// labelled line, the ion's M0, and not from the only formula without a bracket,
// which is the reagent's unlabelled remainder: without the ion formula that line
// read "M0" and the real M0 "[15N]".
describe('formatIsotopeFormula of a labelled ion', () => {
  /** The label of each isotopologue formula in the pattern of `ionFormula`. */
  const labelsOf = (ionFormula, formulas) =>
    formulas.map((formula) => formatIsotopeFormula(formula, ionFormula))

  // The 15N-nitrate ion C9H16O7^N-, lightest line first: the remainder at
  // m/z 250.0932, the M0 at 251.0903, then its 13C and 18O lines. The remainder
  // carries its labelled atom at 14N, and its own 13C line - 6 mDa above the M0 -
  // says both.
  it('counts a 15N-labelled family from its labelled line, the remainder at 14N', () => {
    expect(
      labelsOf('C9H16O7^N-', [
        'C9H16NO7-',
        '[15N]C9H16O7-',
        '[13C]C8H16NO7-',
        '[13C][15N]C8H16O7-',
        '[15N][18O]C9H16O6-'
      ])
    ).toEqual(['[14N]', 'M0', '[13C][14N]', '[13C]', '[18O]'])
  })

  // The 15N nitric acid-nitrate cluster carries two labelled atoms, so a line
  // with one of them at 14N is not its M0 but one step down from it.
  it('counts each labelled atom of a two-label ion', () => {
    expect(labelsOf('HO6^N2-', ['HN2O6-', '[15N]HNO6-', '[15N]2HO6-', '[15N]2[18O]HO5-'])).toEqual([
      '[14N]2',
      '[14N]',
      'M0',
      '[18O]'
    ])
  })

  // In the order the generator writes brackets: carbon, hydrogen, the rest
  // alphabetically.
  it('writes the unlabelled atom where the generator would', () => {
    expect(labelsOf('C9H16O7^N-', ['[18O]C9H16NO6-'])).toEqual(['[14N][18O]'])
    expect(labelsOf('C9H17BrO7^N-', ['[81Br]C9H17NO7-'])).toEqual(['[81Br][14N]'])
  })

  // At a low resolution the M0 and the remainder's 13C line, 6 mDa apart, are one
  // line named by both; a line that holds the M0 is the M0. A merged line without
  // it names each isotopologue's difference from the M0.
  it('reads a merged low-resolution line as the M0 when one of its names is', () => {
    expect(formatIsotopeFormula('[15N]C9H16O7-/[13C]C8H16NO7-', 'C9H16O7^N-')).toBe('M0')
    expect(formatIsotopeFormula('[13C][15N]C8H16O7-/[2H][15N]C9H15O7-', 'C9H16O7^N-')).toBe(
      '[13C]/[2H]'
    )
  })

  // The store's pair: an imported run writes a labelled ion's M0 in the ion's own
  // notation, so the isotopologue formula is the ion formula, caret and all. It
  // names the labelled composition, and a caret atom beside brackets is the
  // label too - the [15N] of the last one is an atom of the analyte's own.
  it('reads a label written as the caret element, as an imported run writes it', () => {
    expect(formatIsotopeFormula('C10H18O7^N-', 'C10H18O7^N-')).toBe('M0')
    expect(formatIsotopeFormula('[13C]C9H18O7^N-', 'C10H18O7^N-')).toBe('[13C]')
    expect(formatIsotopeFormula('HO6^N2-', 'HO6^N2-')).toBe('M0')
    expect(formatIsotopeFormula('C2H3NO5^N-', 'C2H3N^NO5-')).toBe('M0')
    expect(formatIsotopeFormula('[15N]C2H3O5^N-', 'C2H3N^NO5-')).toBe('[15N]')
  })

  // What a call site that passes no ion formula shows: the unlabelled reading.
  it('reads the pattern as an unlabelled ion without the ion formula', () => {
    expect(formatIsotopeFormula('C9H16NO7-')).toBe('M0')
    expect(formatIsotopeFormula('[15N]C9H16O7-')).toBe('[15N]')
  })
})

// An ion with no label keeps the labels it always had: its brackets, and "M0" for
// the formula without one - for bromoform with bromide the lightest line of the
// cluster, while the tallest, with two of the four bromines at 81Br, reads [81Br]2.
describe('formatIsotopeFormula of an unlabelled ion', () => {
  const BROMINE = ['CHBr4-', '[81Br]CHBr3-', '[81Br]2CHBr2-', '[81Br]3CHBr-', '[81Br]4CH-']
  const LABELS = ['M0', '[81Br]', '[81Br]2', '[81Br]3', '[81Br]4']

  it('labels a bromine cluster by its brackets, with or without the ion formula', () => {
    expect(BROMINE.map((formula) => formatIsotopeFormula(formula, 'CHBr4-'))).toEqual(LABELS)
    expect(BROMINE.map((formula) => formatIsotopeFormula(formula))).toEqual(LABELS)
  })

  it.each([
    ['C3H6O3', 'M0'],
    ['[13C]C2H6O3', '[13C]'],
    ['[13C]C2[2H]H5O3', '[13C][2H]'],
    ['[13C]2CH6O3', '[13C]2'],
    ['[13C]C2H6O3/C3H6[18O]O2', '[13C]/[18O]']
  ])('formats %s of its own ion as %s', (formula, expected) => {
    expect(formatIsotopeFormula(formula, 'C3H6O3')).toBe(expected)
  })
})

describe('neutralKey', () => {
  it('reads one neutral the same however it is written', () => {
    expect(neutralKey('C1H4N2O1')).toBe(neutralKey('CH4N2O'))
    expect(neutralKey('C3H7NO')).toBe(neutralKey('C3H7N1O1'))
    // Hill order and alphabetical order, a condensed formula, an explicit zero.
    expect(neutralKey('C2H3ClO')).toBe(neutralKey('C2ClH3O'))
    expect(neutralKey('CH3COOH')).toBe(neutralKey('C2H4O2'))
    expect(neutralKey('C3H7N0O')).toBe(neutralKey('C3H7O'))
  })

  it('tells different neutrals apart', () => {
    expect(neutralKey('C3H7NO')).not.toBe(neutralKey('C3H4O'))
  })

  it('keeps a labelled atom its own', () => {
    expect(neutralKey('C5H9[15N]O7')).not.toBe(neutralKey('C5H9NO7'))
    expect(neutralKey('H^NO3')).not.toBe(neutralKey('HNO3'))
    expect(neutralKey('C5H9[15N]1O7')).toBe(neutralKey('C5H9[15N]O7'))
  })

  it('gives back a formula it cannot read as it was, and nothing for none', () => {
    expect(neutralKey('Ca(OH)2')).toBe('Ca(OH)2')
    expect(neutralKey('')).toBe('')
    expect(neutralKey(null)).toBe('')
  })
})

// The M0 an isotope table opens on and every offset label counts from. The rows
// arrive in m/z order, so for an unlabelled ion - a bromine cluster included -
// the answer is the first one; for a labelled ion the first row is the reagent's
// unlabelled remainder, and the M0 is the labelled line above it.
describe('monoisotopicIsotope', () => {
  const row = (mz, formula) => ({ mz, target_isotope_formula: formula })

  const NITRATE = [
    row(250.0932, 'C9H16NO7-'),
    row(251.0903, '[15N]C9H16O7-'),
    row(252.0936, '[13C][15N]C8H16O7-')
  ]
  const BROMINE = [row(328.6817, 'CHBr4-'), row(330.6797, '[81Br]CHBr3-')]

  it("picks a labelled ion's labelled line over the lighter remainder", () => {
    expect(monoisotopicIsotope(NITRATE, 'C9H16O7^N-')).toBe(NITRATE[1])
  })

  it('picks the lightest line of an unlabelled bromine cluster', () => {
    expect(monoisotopicIsotope(BROMINE, 'CHBr4-')).toBe(BROMINE[0])
  })

  it('reads rows in m/z order whatever order they arrive in', () => {
    expect(monoisotopicIsotope([...NITRATE].reverse(), 'C9H16O7^N-')).toBe(NITRATE[1])
    expect(monoisotopicIsotope([...BROMINE].reverse(), 'CHBr4-')).toBe(BROMINE[0])
  })

  it("leaves the caller's array in the order it was given", () => {
    const rows = [...NITRATE].reverse()
    monoisotopicIsotope(rows, 'C9H16O7^N-')
    expect(rows).toEqual([...NITRATE].reverse())
  })

  it('sorts an m/z the store formatted as a string', () => {
    const formatted = NITRATE.map(({ mz, target_isotope_formula }) => ({
      mz: mz.toFixed(4),
      target_isotope_formula
    }))
    expect(monoisotopicIsotope([...formatted].reverse(), 'C9H16O7^N-')).toBe(formatted[1])
  })

  it('reads a merged low-resolution line that holds the M0 as the M0', () => {
    const merged = [row(250.0932, 'C9H16NO7-'), row(251.0903, '[15N]C9H16O7-/[13C]C8H16NO7-')]
    expect(monoisotopicIsotope(merged, 'C9H16O7^N-')).toBe(merged[1])
  })

  it('falls back to the lightest row when no formula is the M0', () => {
    const heavy = [NITRATE[2], row(253.0937, '[13C]2[15N]C7H16O7-')]
    expect(monoisotopicIsotope([...heavy].reverse(), 'C9H16O7^N-')).toBe(heavy[0])
  })

  it('has no answer without rows', () => {
    expect(monoisotopicIsotope([], 'C9H16O7^N-')).toBeUndefined()
    expect(monoisotopicIsotope(null, 'C9H16O7^N-')).toBeUndefined()
  })
})

describe('compoundIdentity', () => {
  it('trims each field and reads a blank one as none', () => {
    expect(
      compoundIdentity({ inchikey: ' VNWKTOKETHGBQD-UHFFFAOYSA-N ', inchi: '  ', smiles: 'C' })
    ).toEqual({ inchikey: 'VNWKTOKETHGBQD-UHFFFAOYSA-N', inchi: null, smiles: 'C' })
  })

  it('answers no identity for no source', () => {
    expect(compoundIdentity(null)).toEqual({ inchikey: null, inchi: null, smiles: null })
  })
})

describe('identityChanges', () => {
  const stored = { inchikey: 'VNWKTOKETHGBQD-UHFFFAOYSA-N', inchi: 'InChI=1S/CH4/h1H4' }

  it('sends nothing for an identity left as it was', () => {
    expect(identityChanges(stored, { ...stored, inchikey: ` ${stored.inchikey}` })).toEqual({})
  })

  it('sends a field the edit changes, and only that one', () => {
    expect(identityChanges(stored, { ...stored, smiles: 'C' })).toEqual({ smiles: 'C' })
  })

  it('sends an emptied field as null, which clears it', () => {
    expect(identityChanges(stored, { ...stored, inchi: '' })).toEqual({ inchi: null })
  })
})

describe('isValidInchikey', () => {
  it.each(['', null, '  ', 'VNWKTOKETHGBQD-UHFFFAOYSA-N', ' VNWKTOKETHGBQD-UHFFFAOYSA-N '])(
    'accepts %s',
    (value) => {
      expect(isValidInchikey(value)).toBe(true)
    }
  )

  it.each([
    'vnwktoketHGBQD-UHFFFAOYSA-N',
    'VNWKTOKETHGBQD-UHFFFAOYSA',
    'VNWKTOKETHGBQDUHFFFAOYSAN'
  ])('rejects %s', (value) => {
    expect(isValidInchikey(value)).toBe(false)
  })
})

describe('isValidInchi', () => {
  it.each(['', null, 'InChI=1S/CH4/h1H4'])('accepts %s', (value) => {
    expect(isValidInchi(value)).toBe(true)
  })

  it.each(['1S/CH4/h1H4', 'inchi=1S/CH4/h1H4'])('rejects %s', (value) => {
    expect(isValidInchi(value)).toBe(false)
  })
})
