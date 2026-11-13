import { describe, it, expect } from 'vitest'

import {
  holdingReasons,
  holdingTooltip,
  persistenceHold,
  persistenceHoldNote,
  persistenceStands,
  persistenceSummary,
  persistenceTooltip
} from '@/lib/batchPersistence'

const RECORD = {
  share: 0.9167,
  n_present: 11,
  n_samples: 12,
  split: 0.4583,
  formula: 'C9H11NO',
  ion_formula: 'C9H12NO+',
  ionization_mechanism_id: 'mech-h'
}
const HELD = [
  {
    rule: 'persistence_only',
    detail: "searched because its peak is in 11 of its partition's 12 spectra",
    caps: true
  }
]
const LIFTED = [
  {
    rule: 'second_line',
    detail: 'a line of its isotope pattern is committed with it in 1 sample of the batch',
    caps: false
  }
]

describe('persistenceSummary', () => {
  it('names the share the admission rested on', () => {
    expect(persistenceSummary(RECORD)).toBe('Searched on persistence: in 11 of 12 spectra (92 %)')
  })

  it('says nothing for no record', () => {
    expect(persistenceSummary(null)).toBe('')
  })
})

describe('persistenceTooltip', () => {
  it('says why the peak was taken, what it was read as, and that the reading is held', () => {
    const text = persistenceTooltip(RECORD, HELD, 'candidate')
    expect(text).toMatch(/^The batch search took this peak because it is in 11 of 12 spectra/)
    expect(text).toContain("above the batch's split at 46 %, not because of its height.")
    expect(text).toContain('It read it as C9H11NO.')
    expect(text).toMatch(/is held at candidate until an isotope line, another channel or a series/)
    expect(text).not.toContain('lower')
  })

  it('says the reading would be held where the vote already put it below candidate', () => {
    const text = persistenceTooltip(RECORD, HELD, 'below_assignability')
    expect(text).toMatch(/would be held at candidate until an isotope line/)
    expect(text).toMatch(/The samples' vote already puts it lower\.$/)
    expect(text).not.toContain('is held at candidate')
  })

  it('names what keeps the reading at its tier once something saw it', () => {
    const text = persistenceTooltip(RECORD, LIFTED, 'assigned')
    expect(text).not.toContain('held at candidate')
    expect(text).toMatch(/Kept its tier on: a line of its isotope pattern is committed/)
  })

  it("reads a row's record's own reasons and tier when none are passed", () => {
    expect(persistenceTooltip({ ...RECORD, tier_reasons: HELD })).toMatch(/is held at candidate/)
    expect(
      persistenceTooltip({ ...RECORD, tier_reasons: HELD, consensus_tier: 'below_assignability' })
    ).toMatch(/would be held at candidate/)
  })

  it('says the search found nothing where it elected no reading', () => {
    const text = persistenceTooltip({ ...RECORD, formula: null }, null)
    expect(text).toMatch(/It found no composition of its own for it\.$/)
  })

  it('says nothing for no record', () => {
    expect(persistenceTooltip(null)).toBe('')
  })
})

describe('the reasons that hold a batch peak', () => {
  it('are the capping ones, named as a run names them', () => {
    expect(holdingReasons(HELD).map((reason) => reason.label)).toEqual(['persistence only'])
    expect(holdingReasons(LIFTED)).toEqual([])
    expect(holdingReasons(null)).toEqual([])
    expect(holdingTooltip(LIFTED, 'assigned')).toBe('')
  })

  it("word what the cap did off the batch peak's tier, as a run's reasons are", () => {
    expect(holdingTooltip(HELD, 'candidate')).toBe(
      'Holds this row at candidate - it cannot be assigned while this stands ' +
        `(persistence only): ${HELD[0].detail}.`
    )
    expect(holdingTooltip(HELD, 'below_assignability')).toBe(
      'Would hold this row at candidate, but its evidence already puts it lower ' +
        `(persistence only): ${HELD[0].detail}.`
    )
  })

  it('say whether the persistence rule stands on the reading', () => {
    expect(persistenceStands(HELD)).toBe(true)
    expect(persistenceStands(LIFTED)).toBe(false)
    expect(persistenceStands(null)).toBe(false)
    // Another rule that holds the tier is not this one.
    expect(persistenceStands([{ rule: 'evidence_band', detail: '...', caps: false }])).toBe(false)
    expect(persistenceStands([{ rule: 'flat_series', detail: '...', caps: true }])).toBe(false)
  })

  it('say what the rule does to the reading at its tier', () => {
    expect(persistenceHold(HELD, 'candidate')).toBe('held')
    expect(persistenceHold(HELD, 'below_assignability')).toBe('lower')
    expect(persistenceHold(LIFTED, 'assigned')).toBe('')
    expect(persistenceHold(null, 'candidate')).toBe('')
    expect(persistenceHoldNote(HELD, 'candidate')).toBe('held at candidate')
    expect(persistenceHoldNote(HELD, 'below_assignability')).toBe('already below candidate')
    expect(persistenceHoldNote(LIFTED, 'assigned')).toBe('')
  })
})
