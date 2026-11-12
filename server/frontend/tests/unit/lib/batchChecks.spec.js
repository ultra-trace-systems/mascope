import { describe, expect, it } from 'vitest'

import {
  TONE_HOLDS,
  TONE_REFUTES,
  TONE_UNREAD,
  checkTags,
  checksRank,
  checksSummary,
  checksTooltip
} from '@/lib/batchChecks'

const lock = (verdict, extra = {}) => ({
  element: 'Cl',
  label: '37Cl',
  n_ion: 1,
  n_neutral: 1,
  judged: 6,
  holds: 6,
  share: 1,
  offset_error_ppm: 0.2,
  window_ppm: 1,
  verdict,
  reason: null,
  ...extra
})
const twin = (verdict, extra = {}) => ({ judged: 6, present: 6, excess: 1, verdict, ...extra })
const pair = (verdict, extra = {}) => ({
  partner_channel: '[M+CH4N2O+H]+',
  r: 0.82,
  n_spectra: 24,
  verdict,
  reason: null,
  ...extra
})

describe('batch checks tags', () => {
  it('tags each fact the record carries, in a fixed order', () => {
    const tags = checkTags({
      neutral_pair: pair('established'),
      halogen_lock: lock('locked'),
      label_twin: twin('present')
    })
    expect(tags.map((tag) => [tag.key, tag.label, tag.tone])).toEqual([
      ['halogen_lock', 'Cl lock', TONE_HOLDS],
      ['label_twin', '14N twin', TONE_HOLDS],
      ['neutral_pair', 'urea pair', TONE_HOLDS]
    ])
  })

  it.each([
    [{ halogen_lock: lock('silicon') }, 'Si, not Cl', TONE_REFUTES],
    [{ halogen_lock: lock('not_locked', { reason: 'ratio' }) }, 'no Cl lock', TONE_REFUTES],
    [{ halogen_lock: lock('untestable', { element: 'Br' }) }, 'Br lock?', TONE_UNREAD],
    [{ label_twin: twin('absent') }, 'no 14N twin', TONE_REFUTES],
    [{ label_twin: twin('untestable') }, '14N twin?', TONE_UNREAD],
    [{ neutral_pair: pair('not_established') }, 'pair apart', TONE_REFUTES],
    [{ neutral_pair: pair('untestable') }, 'pair?', TONE_UNREAD]
  ])('reads %o as %s', (record, label, tone) => {
    expect(checkTags(record)).toEqual([expect.objectContaining({ label, tone })])
  })

  it('has nothing for no record', () => {
    expect(checkTags(null)).toEqual([])
    expect(checksSummary(null)).toBe('')
    expect(checksTooltip(null)).toBe('')
    expect(checksTooltip({ formula: 'X' })).toBe('')
  })

  it('ranks the facts that hold over those that refute, none read as null', () => {
    expect(checksRank({ halogen_lock: lock('locked'), label_twin: twin('present') })).toBe(2)
    expect(checksRank({ halogen_lock: lock('locked'), label_twin: twin('absent') })).toBe(0)
    expect(checksRank({ label_twin: twin('untestable') })).toBe(0)
    expect(checksRank({ neutral_pair: pair('not_established') })).toBe(-1)
    expect(checksRank(null)).toBeNull()
  })

  it("sums the facts up for the inspector's line", () => {
    expect(checksSummary({ halogen_lock: lock('locked'), neutral_pair: pair('untestable') })).toBe(
      'Batch facts: Cl lock, pair?'
    )
  })
})

describe('batch checks tooltip', () => {
  const said = (record) => checksTooltip(record)

  it('says what a lock holds, and whose the atoms are', () => {
    expect(said({ halogen_lock: lock('locked') })).toBe(
      'Halogen lock: its 37Cl line holds the share of 1 chlorine at the exact spacing, ' +
        'in 6 of the 6 samples that could tell (1 chlorine in the compound). ' +
        'Shown for reading only: no tier moves on it.'
    )
    expect(
      said({ halogen_lock: lock('locked', { element: 'Br', label: '81Br', n_neutral: 0 }) })
    ).toMatch(/1 bromine at the exact spacing.*\(the reagent's, not the compound's\)/)
  })

  it.each([
    ['silicon', null, /the line is silicon's/],
    ['not_locked', 'not_found', /no batch peak stands at its 37Cl line/],
    ['not_locked', 'another_parent', /another compound's isotope line/],
    ['not_locked', 'offset', /0\.2 ppm off the exact spacing, outside the batch's 1 ppm/],
    ['not_locked', 'ratio', /does not hold the share of 1 chlorine across the batch/],
    ['untestable', 'both_halogens', /chlorine and bromine, whose lines share one M\+2 peak/],
    ['untestable', 'unresolved', /not separated from another line/],
    ['untestable', 'silicon_untestable', /too few samples show the 29Si line/],
    [
      'untestable',
      'no_offset',
      /no sample its 37Cl line was stamped in for this compound still holds both lines, so its spacing was not read\./
    ],
    ['untestable', 'few_spectra', /too few samples can tell its 37Cl line/]
  ])('reads a %s lock (%s) in words', (verdict, reason, words) => {
    expect(said({ halogen_lock: lock(verdict, { reason }) })).toMatch(words)
  })

  it('says where a twin stands, and how far its 14N line runs above the impurity', () => {
    expect(said({ label_twin: twin('present') })).toMatch(
      /stands at its share in 6 of the 6 samples that would show it\./
    )
    expect(said({ label_twin: twin('present', { excess: 3.04 }) })).toMatch(
      /the 14N line running 3 times the impurity's share/
    )
    expect(said({ label_twin: twin('absent', { present: 1 }) })).toMatch(
      /missing from 5 of the 6 samples that would show it, so this ion is not the label's/
    )
    expect(said({ label_twin: twin('present', { impurity_factor: 0.41 }) })).toMatch(
      /The share is the one the reagent's own ions show, 41% of what the setup's purity gives\./
    )
    expect(said({ label_twin: twin('present') })).not.toMatch(/reagent's own ions/)
    expect(said({ label_twin: twin('untestable', { judged: 2 }) })).toMatch(
      /too few samples would show the label's impurity line \(2\)/
    )
  })

  it('reads a pair with its r', () => {
    expect(said({ neutral_pair: pair('established') })).toMatch(
      /\[M\+CH4N2O\+H\]\+ peak co-varies with this one across the batch: r 0\.82 over 24 spectra/
    )
    expect(said({ neutral_pair: pair('not_established', { r: 0.1 }) })).toMatch(
      /does not co-vary with this one: r 0\.10/
    )
    expect(said({ neutral_pair: pair('untestable', { reason: 'flat', r: null }) })).toMatch(
      /hardly moves across the batch/
    )
    expect(said({ neutral_pair: pair('untestable', { reason: 'few_spectra' }) })).toMatch(
      /share too few spectra/
    )
  })
})
