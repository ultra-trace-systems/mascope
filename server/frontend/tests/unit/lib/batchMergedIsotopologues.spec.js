import { describe, expect, it } from 'vitest'

import {
  strippedLabel,
  strippedLinesCount,
  strippedLinesSummary,
  strippedLinesTooltip,
  strippedParentId,
  strippedTooltip
} from '@/lib/batchMergedIsotopologues'

const RECORD = {
  batch_peak_id: 'bp-p',
  formula: 'C10H16O4',
  label: '13C',
  ratio: 0.1093,
  spectra: 6,
  median_factor: 0.97,
  above: 0,
  displaced: { formula: 'C9H12N2O2' }
}

const PARENT = {
  formula: 'C10H16O4',
  n_lines: 2,
  lines: [
    { label: '13C', displaced_formula: 'C9H12N2O2', median_factor: 0.97, spectra: 6 },
    { label: '18O', displaced_formula: null, median_factor: 1.2, spectra: 1 }
  ]
}

describe('batchMergedIsotopologues', () => {
  it('reads the parent and the line off a stripped row', () => {
    const row = { merged_isotopologue_of: RECORD }
    expect(strippedParentId(row)).toBe('bp-p')
    expect(strippedLabel(row)).toBe('13C')
    expect(strippedParentId({})).toBeNull()
    expect(strippedLabel(null)).toBe('')
  })

  it("says whose line a stripped row is, what its samples' runs read and how it held", () => {
    expect(strippedTooltip(RECORD)).toBe(
      'Stripped from the ledger as the 13C isotopologue of C10H16O4 (10.9% of its height): ' +
        "the samples' runs read this peak as C9H12N2O2, and across the batch its height was " +
        "0.97 times the line's share (the median over 6 samples, none above twice it). " +
        "Its samples were rewritten as that peak's isotope line."
    )
    expect(strippedTooltip({ ...RECORD, above: 1, displaced: null })).toContain(
      'as a compound of its own'
    )
    expect(strippedTooltip({ ...RECORD, above: 1 })).toContain('1 of them above twice it')
    expect(strippedTooltip(null)).toBe('')
  })

  it('counts and lists the rows a parent took', () => {
    expect(strippedLinesCount(PARENT)).toBe(2)
    expect(strippedLinesCount({ lines: [{}] })).toBe(1)
    expect(strippedLinesCount(null)).toBe(0)
    expect(strippedLinesSummary(PARENT)).toBe("2 rows stripped to this compound's isotope lines")
    expect(strippedLinesSummary({ n_lines: 1 })).toBe(
      "1 row stripped to this compound's isotope lines"
    )
    expect(strippedLinesTooltip(PARENT)).toBe(
      "2 rows stripped to this compound's isotope lines: " +
        '13C: read as C9H12N2O2, at 0.97 times its share over 6 samples. ' +
        '18O: read on its own, at 1.2 times its share over 1 sample. ' +
        'Unfold the isotopologues to see them under it.'
    )
    expect(strippedLinesTooltip(null)).toBe('')
  })
})
