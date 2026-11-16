import { describe, expect, it } from 'vitest'

import {
  FLAT,
  flatSeriesKind,
  flatSeriesOf,
  flatSeriesSummary,
  flatSeriesTooltip
} from '@/lib/batchFlatSeries'

const record = (verdict, extra = {}) => ({
  formula: 'C2H3N3',
  verdict,
  cv: 0.0812,
  n_present: 22,
  n_spectra: 24,
  share: 0.9167,
  normaliser: 'tic',
  normalised: true,
  source_ion: null,
  reason: null,
  ...extra
})
const SOURCE = record(FLAT, {
  formula: 'NO2',
  source_ion: { ion_formula: 'NO2-', label: '[NO2]-' }
})

describe('flatSeriesOf', () => {
  it('is the record where the trace reads flat, and nothing otherwise', () => {
    expect(flatSeriesOf({ flat_series: record(FLAT) })).toEqual(record(FLAT))
    expect(flatSeriesOf({ flat_series: record('varying') })).toBeNull()
    expect(flatSeriesOf({ flat_series: null })).toBeNull()
    expect(flatSeriesOf(null)).toBeNull()
  })
})

describe('flatSeriesKind', () => {
  it("marks a flat ion the source's list names", () => {
    expect(flatSeriesKind(SOURCE)).toBe('source')
    expect(flatSeriesKind(record(FLAT))).toBe('')
    expect(flatSeriesKind({ ...SOURCE, verdict: 'varying' })).toBe('')
    expect(flatSeriesKind(null)).toBe('')
  })
})

describe('flatSeriesSummary', () => {
  it('says the cv and the share, and the source ion where one is named', () => {
    expect(flatSeriesSummary(record(FLAT))).toBe('Flat series: cv 0.08 in 92 % of the samples')
    expect(flatSeriesSummary(SOURCE)).toBe(
      'Flat series: cv 0.08 in 92 % of the samples, a [NO2]- ion of the source'
    )
    expect(flatSeriesSummary(record('varying', { cv: 0.613 }))).toBe(
      'Varies across the batch: cv 0.61'
    )
    expect(flatSeriesSummary(record('untestable', { reason: 'rare' }))).toBe('Flatness not read')
    expect(flatSeriesSummary(null)).toBe('')
  })
})

describe('flatSeriesTooltip', () => {
  it('names the normaliser, what it found, and that the tier stands', () => {
    const flat = flatSeriesTooltip(record(FLAT))
    expect(flat).toMatch(/^Its trace was read against the samples' total ion current, over 22/)
    expect(flat).toMatch(/a cv of 0\.08: likely a background of the source or the inlet/)
    expect(flat).toMatch(/not whether the formula is right: the tier stands\.$/)
    expect(flat).not.toMatch(/source's list/)
    expect(flatSeriesTooltip(SOURCE)).toMatch(/The source's list names its ion, \[NO2\]-\./)
  })

  it('says where the traces were read undivided, or with the reagent ions', () => {
    expect(
      flatSeriesTooltip(record(FLAT, { normalised: false, normaliser_rejected: true }))
    ).toMatch(/read against nothing: the normaliser moves too much/)
    expect(flatSeriesTooltip(record(FLAT, { normaliser: 'reagent' }))).toMatch(
      /read against the batch's reagent ions/
    )
  })

  it('says a batch with no normaliser at all was read on the raw heights, not rejected', () => {
    const none = flatSeriesTooltip(
      record(FLAT, { normaliser: null, normalised: false, normaliser_rejected: false })
    )
    expect(none).toMatch(
      /read against nothing: the batch holds neither reagent ions nor a total ion current,/
    )
    expect(none).not.toMatch(/moves too much/)
  })

  it('says a varying trace follows what was sampled, and why one was not read', () => {
    expect(flatSeriesTooltip(record('varying', { cv: 0.61 }))).toMatch(
      /varies, a cv of 0\.61: it follows what was sampled\.$/
    )
    expect(flatSeriesTooltip(record('untestable', { reason: 'steady_batch' }))).toMatch(
      /Not read: the batch holds still/
    )
    expect(flatSeriesTooltip(null)).toBe('')
  })
})
