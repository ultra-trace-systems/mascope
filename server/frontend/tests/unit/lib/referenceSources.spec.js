import { describe, it, expect } from 'vitest'

import {
  REFERENCE_LISTS_GUIDE_URL,
  loadedLabel,
  polarityLabel,
  referenceUrl,
  windowLabel
} from '@/lib/referenceSources'

// How the Reference lists dialog words a source's columns and links a
// species' reference.

describe('windowLabel', () => {
  it('reads as the server describes a window, C and H first', () => {
    expect(
      windowLabel({ elements: ['O', 'S', 'N', 'H', 'C'], max_carbon: 40, max_mass: 700 })
    ).toBe('C, H, N, O, S; C <= 40; <= 700 Da')
  })

  it('names each open axis', () => {
    expect(windowLabel({ elements: null, max_carbon: 20, max_mass: null })).toBe(
      'any element; C <= 20; any mass'
    )
    expect(windowLabel({ elements: ['O', 'Si'], max_carbon: null, max_mass: 450.5 })).toBe(
      'O, Si; any carbon count; <= 450.5 Da'
    )
    // Carbon and hydrogen lead even where the alphabet would not put them.
    expect(
      windowLabel({ elements: ['Cl', 'H', 'Br', 'C'], max_carbon: null, max_mass: null })
    ).toBe('C, H, Br, Cl; any carbon count; any mass')
  })

  it('says unbounded for a window that bounds nothing, or none at all', () => {
    expect(windowLabel({ elements: null, max_carbon: null, max_mass: null })).toBe('Unbounded')
    expect(windowLabel(null)).toBe('Unbounded')
    expect(windowLabel(undefined)).toBe('Unbounded')
  })
})

describe('polarityLabel', () => {
  it('names a polarity, and both for a source row that records none', () => {
    expect(polarityLabel('negative')).toBe('Negative')
    expect(polarityLabel('positive')).toBe('Positive')
    expect(polarityLabel(null)).toBe('Both')
    expect(polarityLabel('both')).toBe('Both')
  })
})

describe('loadedLabel', () => {
  it('shows a timestamp in the locale, and a dash for none or a bad one', () => {
    const stamp = '2026-10-20T08:30:00+00:00'
    expect(loadedLabel(stamp)).toBe(new Date(stamp).toLocaleString())
    expect(loadedLabel(null)).toBe('-')
    expect(loadedLabel('not a date')).toBe('-')
  })
})

describe('referenceUrl', () => {
  it('links a DOI to doi.org, however it is written', () => {
    expect(referenceUrl('10.5194/acp-21-1-2021')).toBe('https://doi.org/10.5194/acp-21-1-2021')
    expect(referenceUrl('doi: 10.5194/acp-21-1-2021')).toBe('https://doi.org/10.5194/acp-21-1-2021')
    expect(referenceUrl('https://doi.org/10.1021/acs.est.0c01234')).toBe(
      'https://doi.org/10.1021/acs.est.0c01234'
    )
    expect(referenceUrl('http://dx.doi.org/10.1021/acs.est.0c01234')).toBe(
      'https://doi.org/10.1021/acs.est.0c01234'
    )
  })

  it('links nothing that is not a DOI', () => {
    expect(referenceUrl('S. Kang, thesis, 2021')).toBeNull()
    expect(referenceUrl('')).toBeNull()
    expect(referenceUrl(null)).toBeNull()
  })
})

describe('REFERENCE_LISTS_GUIDE_URL', () => {
  it('points at the user guide in the docs the app serves', () => {
    expect(REFERENCE_LISTS_GUIDE_URL).toBe('/docs/guides/reference-lists/')
  })
})
