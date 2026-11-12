import { describe, it, expect } from 'vitest'

import {
  GRADES,
  continuityLabel,
  continuityPartial,
  continuityRank,
  continuityStale,
  continuityTooltip,
  continuityWarning,
  continuityWithdrawn,
  pairLine,
  pairsShownNote
} from '@/lib/batchContinuity'

const detail = (extra = {}) => ({
  eligible: true,
  reason: null,
  formula: 'C6H12O6',
  ionization_mechanism_id: null,
  n_present_at_compute: 40,
  n_pairs: 2,
  warnings: [],
  pairs: [],
  ...extra
})
const row = (grade, extra = {}) => ({
  consensus_formula: 'C6H12O6',
  n_present: 40,
  continuity_grade: grade,
  continuity: detail(),
  continuity_stale: false,
  ...extra
})

describe('batchContinuity labels and rank', () => {
  it('names every grade, and a batch whose grades were withdrawn', () => {
    expect(GRADES.map((grade) => continuityLabel(row(grade)))).toEqual([
      'Red',
      'Drift',
      'Stable',
      'Inconclusive',
      'Insufficient'
    ])
    const withdrawn = row(null, { continuity: detail({ eligible: false, reason: 'gap' }) })
    expect(continuityWithdrawn(withdrawn)).toBe(true)
    expect(continuityLabel(withdrawn)).toBe('Not graded')
    expect(continuityLabel({})).toBe('')
    expect(continuityWithdrawn({})).toBe(false)
  })

  it('ranks in the order of alarm, null where there is no grade', () => {
    expect(GRADES.map((grade) => continuityRank(row(grade)))).toEqual([0, 1, 2, 3, 4])
    expect(continuityRank(row(null))).toBeNull()
    expect(continuityRank(null)).toBeNull()
  })

  it('reads the partial warning and the stale mark off the row', () => {
    expect(continuityPartial(row('stable'))).toBe(false)
    expect(
      continuityPartial(row('stable', { continuity: detail({ warnings: ['partial'] }) }))
    ).toBe(true)
    expect(continuityStale(row('stable'))).toBe(false)
    expect(continuityStale(row('stable', { continuity_stale: true }))).toBe(true)
  })
})

describe('batchContinuity tooltip and warning', () => {
  it("gives the grade's meaning over its pairs, and that it is shown only", () => {
    const text = continuityTooltip(row('red'))
    expect(text).toMatch(/^Red on its most alarming of 2 pairs: .*two species may share a trace\./)
    expect(text).toMatch(/never which species, and no tier moves on it\.$/)
    expect(continuityWarning(row('red'))).toBe('')
  })

  it('says a stale grade was graded at another prevalence', () => {
    const stale = row('stable', { continuity_stale: true, n_present: 43 })
    expect(continuityWarning(stale)).toMatch(/^Graded at 40 samples - the batch peak is in 43 now/)
    expect(continuityTooltip(stale)).toContain('Graded at 40 samples')
  })

  it('says a stale grade was graded for another formula', () => {
    const stale = row('stable', { continuity_stale: true, consensus_formula: 'C7H8' })
    expect(continuityWarning(stale)).toMatch(/^Graded for C6H12O6 - the consensus is now C7H8\./)
  })

  it('says why a batch was not graded', () => {
    const gap = row(null, { continuity: detail({ eligible: false, reason: 'gap' }) })
    expect(continuityWarning(gap)).toMatch(/not time-ordered: its largest gap/)
    const few = row(null, { continuity: detail({ eligible: false, reason: 'few_times' }) })
    expect(continuityWarning(few)).toMatch(/too few distinct acquisition times/)
    expect(continuityTooltip(gap)).not.toMatch(/most alarming/)
  })

  it('names a partial grade', () => {
    const partial = row('stable', { continuity: detail({ warnings: ['partial'] }) })
    expect(continuityTooltip(partial)).toMatch(/Partial: the batch had more pairs/)
    expect(continuityTooltip(row('stable'))).not.toMatch(/Partial/)
  })

  it('is empty for a row the pass left nothing on', () => {
    expect(continuityTooltip({})).toBe('')
    expect(continuityWarning({})).toBe('')
  })
})

describe('batchContinuity pair lines', () => {
  const pair = (extra = {}) => ({
    kind: 'adduct',
    partner_batch_peak_id: 'bp-2',
    partner_mz: 262.05694,
    grade: 'drift',
    n: 41,
    ratio_median: 0.4213,
    ratio_spread: 0.083,
    rho: -0.71,
    drift_log: -0.9,
    drift_fold: 2.46,
    co_variation: { verdict: 'co_varying', r: 0.82, n_spectra: 41 },
    ...extra
  })

  it('lists a channel pair with its ratio, spread, drift and co-variation', () => {
    expect(pairLine(pair())).toEqual({
      key: 'adduct:bp-2',
      partner: 'Other channel',
      mz: '262.0569',
      grade: 'drift',
      gradeLabel: 'Drift',
      samples: 41,
      ratio: '0.421',
      spread: '8%',
      drift: 'falls 2.46x, rho -0.71',
      coVariation: 'co-varies, r 0.82'
    })
  })

  it('names the isotope partner from either side', () => {
    expect(pairLine(pair({ kind: 'isotopologue' })).partner).toBe('Isotope line')
    expect(pairLine(pair({ kind: 'isotopologue' }), true).partner).toBe('Main peak')
  })

  it('says a drift too short to test was not tested, and a missing reading is a dash', () => {
    const line = pairLine(
      pair({
        drift_log: null,
        drift_fold: null,
        rho: null,
        ratio_median: null,
        ratio_spread: null,
        co_variation: null,
        drift_tested: false
      })
    )
    expect(line.drift).toBe('not tested')
    expect(line.ratio).toBe('-')
    expect(line.spread).toBe('-')
    expect(line.coVariation).toBeNull()
  })

  it('reads a rising drift and the two other co-variation verdicts', () => {
    expect(pairLine(pair({ drift_log: 0.5, drift_fold: 1.65 })).drift).toBe(
      'rises 1.65x, rho -0.71'
    )
    expect(
      pairLine(pair({ co_variation: { verdict: 'not_co_varying', r: 0.1 } })).coVariation
    ).toBe('does not co-vary, r 0.10')
    expect(pairLine(pair({ co_variation: { verdict: 'untestable', r: null } })).coVariation).toBe(
      'co-variation untestable'
    )
  })

  it('notes when the detail lists fewer pairs than the anchor has', () => {
    expect(
      pairsShownNote(row('red', { continuity: detail({ n_pairs: 7, pairs: [{}, {}] }) }))
    ).toBe('Showing 2 of 7 pairs: the most alarming first, then the best supported.')
    expect(
      pairsShownNote(row('red', { continuity: detail({ n_pairs: 2, pairs: [{}, {}] }) }))
    ).toBe('')
  })
})
