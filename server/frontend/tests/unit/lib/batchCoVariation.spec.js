import { describe, it, expect } from 'vitest'

import {
  MIN_SPECTRA,
  coVariationLabel,
  coVariationRank,
  coVariationSummary,
  coVariationTooltip
} from '@/lib/batchCoVariation'

// Worded like the run's outcome (`batch_coherence.co_variation_outcome`): the
// strongest partner first, every partner named by its channel, and the reading
// said to be display-only.

const partner = (channel, verdict, extra = {}) => ({
  batch_peak_id: `bp-${channel}`,
  channel,
  verdict,
  r: null,
  n_spectra: 400,
  reason: null,
  ...extra
})

const record = (partners, extra = {}) => ({
  formula: 'C3H4O4',
  channel: '[M-H]-',
  ...partners[0],
  partner_batch_peak_id: partners[0].batch_peak_id,
  partner_channel: partners[0].channel,
  partners,
  normalised: true,
  ...extra
})

describe('the co-variation reading in words', () => {
  it('mirrors the server floor', () => {
    expect(MIN_SPECTRA).toBe(10)
  })

  it('labels the ledger cell with the strongest partner r', () => {
    const covaries = record([partner('[M+NO3]-', 'co_varying', { r: 0.8234 })])
    expect(coVariationLabel(covaries)).toBe('r 0.82')
    const against = record([partner('[M+NO3]-', 'not_co_varying', { r: -0.871 })])
    expect(coVariationLabel(against)).toBe('r -0.87')
    const flat = record([partner('[M+NO3]-', 'untestable', { reason: 'flat' })])
    expect(coVariationLabel(flat)).toBe('untestable')
    expect(coVariationLabel(null)).toBe('')
  })

  it('sorts on r and leaves an untestable or missing reading unranked', () => {
    expect(coVariationRank(record([partner('[M+NO3]-', 'co_varying', { r: 0.7 })]))).toBe(0.7)
    expect(coVariationRank(record([partner('[M+NO3]-', 'not_co_varying', { r: -0.2 })]))).toBe(-0.2)
    expect(
      coVariationRank(record([partner('[M+NO3]-', 'untestable', { reason: 'few_spectra' })]))
    ).toBeNull()
    expect(coVariationRank(undefined)).toBeNull()
  })

  it('sums it up for the inspector', () => {
    expect(coVariationSummary(record([partner('[M+NO3]-', 'co_varying', { r: 0.9 })]))).toBe(
      'Co-varies with [M+NO3]- across the batch'
    )
    expect(coVariationSummary(record([partner('[M+NO3]-', 'not_co_varying', { r: -0.9 })]))).toBe(
      'Does not co-vary with [M+NO3]- across the batch'
    )
    expect(
      coVariationSummary(record([partner('[M+NO3]-', 'untestable', { reason: 'flat' })]))
    ).toBe('Co-variation with [M+NO3]- cannot be read')
    expect(coVariationSummary(null)).toBe('')
  })

  it('names every partner channel, strongest first, and says no tier moves', () => {
    const text = coVariationTooltip(
      record([
        partner('[M+NO3]-', 'co_varying', { r: 0.81, n_spectra: 412 }),
        partner('[M+Br]-', 'not_co_varying', { r: -0.3, n_spectra: 1 }),
        partner('[M+Cl]-', 'untestable', { reason: 'few_spectra', n_spectra: 6 }),
        partner('[M+I]-', 'untestable', { reason: 'flat', n_spectra: 300 }),
        partner('[M+F]-', 'untestable', { reason: 'few_spectra', n_spectra: 0 })
      ])
    )
    expect(text).toBe(
      'Co-varies with its [M+NO3]- channel across the batch: r 0.81 over 412 spectra. ' +
        'Does not co-vary with its [M+Br]- channel across the batch: r -0.30 over 1 spectrum. ' +
        'Its co-variation with its [M+Cl]- channel cannot be read: the two are seen together ' +
        'in 6 spectra only, fewer than the 10 the reading needs. ' +
        'Its co-variation with its [M+I]- channel cannot be read: one of the two hardly ' +
        'moves across the batch. ' +
        'Its co-variation with its [M+F]- channel cannot be read: the two are never seen in ' +
        'the same spectrum. ' +
        "Log intensities, divided by each spectrum's reagent signal, with the batch's common " +
        'mode taken out. Shown for reading only: no tier moves on it.'
    )
  })

  it('says when there was no reagent ion to normalise by', () => {
    const text = coVariationTooltip(
      record([partner('[M+NO3]-', 'co_varying', { r: 0.81 })], { normalised: false })
    )
    expect(text).toContain('the batch peaks hold no reagent ion to normalise by')
  })

  it('reads a record with no partner list off its top', () => {
    const text = coVariationTooltip({
      verdict: 'co_varying',
      r: 0.7,
      n_spectra: 20,
      partner_channel: '[M+NO3]-'
    })
    expect(text).toMatch(/^Co-varies with its \[M\+NO3\]- channel across the batch: r 0\.70/)
    expect(coVariationTooltip(null)).toBe('')
  })
})
