import { describe, it, expect } from 'vitest'

import {
  heldOnPersistence,
  holdingReasons,
  holdingTooltip,
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
    const text = persistenceTooltip(RECORD, HELD)
    expect(text).toMatch(/^The batch search took this peak because it is in 11 of 12 spectra/)
    expect(text).toContain("above the batch's split at 46 %, not because of its height.")
    expect(text).toContain('It read it as C9H11NO.')
    expect(text).toMatch(/held at candidate until an isotope line, another channel or a series/)
  })

  it('names what keeps the reading at its tier once something saw it', () => {
    const text = persistenceTooltip(RECORD, LIFTED)
    expect(text).not.toContain('held at candidate')
    expect(text).toMatch(/Kept its tier on: a line of its isotope pattern is committed/)
  })

  it("reads a row's record's own reasons when none are passed", () => {
    expect(persistenceTooltip({ ...RECORD, tier_reasons: HELD })).toMatch(/held at candidate/)
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
    expect(holdingTooltip(HELD)).toBe(`Held at candidate, persistence only: ${HELD[0].detail}.`)
    expect(holdingTooltip(LIFTED)).toBe('')
  })

  it('say whether the persistence rule holds the reading', () => {
    expect(heldOnPersistence(HELD)).toBe(true)
    expect(heldOnPersistence(LIFTED)).toBe(false)
    expect(heldOnPersistence(null)).toBe(false)
    // Another rule that holds the tier is not this one.
    expect(heldOnPersistence([{ rule: 'evidence_band', detail: '...', caps: false }])).toBe(false)
    expect(heldOnPersistence([{ rule: 'flat_series', detail: '...', caps: true }])).toBe(false)
  })
})
