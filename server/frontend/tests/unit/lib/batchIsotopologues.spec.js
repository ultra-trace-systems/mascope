import { describe, expect, it } from 'vitest'

import {
  isPredicted,
  stampLabel,
  stampParentId,
  isotopologuesSummary,
  isotopologuesTooltip,
  stampTooltip
} from '@/lib/batchIsotopologues'

const STAMP = {
  batch_peak_id: 'bp-parent',
  formula: 'C2H4O2',
  label: '81Br',
  ratio: 0.9728,
  judged: 6,
  holds: 6,
  share: 1,
  n_predicted: 4,
  n_picker: 2
}

const PARENT = {
  formula: 'C2H4O2',
  n_predicted: 5,
  n_found: 3,
  n_stamped: 1,
  predicted: [
    { label: '81Br', stamped: true, judged: 6, holds: 6, share: 1, verdict: 'stamped' },
    { label: '18O', stamped: false, reason: 'not_found', verdict: 'untestable' },
    { label: '15N', stamped: false, reason: 'unresolved', verdict: 'untestable' },
    { label: '34S', stamped: false, judged: 8, holds: 2, share: 0.25, verdict: 'not_stamped' },
    {
      label: '37Cl',
      stamped: false,
      reason: 'another_parent',
      verdict: 'stamped',
      taken_by: 'bp-x'
    }
  ]
}

describe('batchIsotopologues', () => {
  it('names the parent and the isotope of a stamped line', () => {
    expect(stampParentId({ stamped_isotopologue_of: STAMP })).toBe('bp-parent')
    expect(stampLabel({ stamped_isotopologue_of: STAMP })).toBe('81Br')
    expect(stampParentId({ stamped_isotopologue_of: null })).toBeNull()
    expect(stampParentId(null)).toBeNull()
    expect(stampLabel({})).toBe('')
  })

  it('tells a predicted stamp from one the peak picker found', () => {
    expect(isPredicted(STAMP)).toBe(true)
    expect(isPredicted({ ...STAMP, n_predicted: 0 })).toBe(false)
    expect(isPredicted(null)).toBe(false)
  })

  it('says whose isotopologue a line is, how well it held and where it came from', () => {
    expect(stampTooltip(STAMP)).toBe(
      'Stamped as the 81Br isotopologue of C2H4O2 (97.3% of its height): its height held in 6 ' +
        'of the 6 samples that could tell (100%), predicted by the stamping in 4 samples and ' +
        'found by the peak picker in 2. Shown for reading only: no tier moves on it.'
    )
    expect(stampTooltip({ ...STAMP, n_picker: 0 })).toMatch(
      /predicted by the stamping in all 4 samples\./
    )
    expect(stampTooltip({ ...STAMP, n_predicted: 0 })).toMatch(
      /found by the peak picker in all 2 samples, and confirmed\./
    )
    expect(stampTooltip(null)).toBe('')
  })

  it('lists every isotope line predicted, found and stamped on the parent', () => {
    expect(isotopologuesSummary(PARENT)).toBe('1 of 5 isotope lines stamped')
    const text = isotopologuesTooltip(PARENT)
    expect(text).toMatch(/^Isotope lines predicted for C2H4O2: 1 of 5 isotope lines stamped\./)
    expect(text).toMatch(/81Br: stamped, held in 6 of 6 samples \(100%\)\./)
    expect(text).toMatch(/18O: predicted, no batch peak at its mass\./)
    expect(text).toMatch(/15N: not separated from another line at this resolution\./)
    expect(text).toMatch(/34S: found, held in 2 of 8 samples only\./)
    expect(text).toMatch(/37Cl: held, but the line is another peak's isotopologue\./)
    expect(text).toMatch(/no tier moves on it\.$/)
    expect(isotopologuesTooltip(null)).toBe('')
  })

  it('says when a found line could not be judged', () => {
    const record = {
      ...PARENT,
      predicted: [{ label: '34S', stamped: false, verdict: 'untestable', reason: 'few_spectra' }]
    }
    expect(isotopologuesTooltip(record)).toMatch(/34S: found, in too few samples that can tell\./)
  })
})
