import { describe, it, expect } from 'vitest'

import {
  TIER_REASON_LABELS,
  reasonIcon,
  reasonTooltip,
  tierReasonLabel,
  tierReasonsOf
} from '@/lib/tierReasons'

// The rules the backend's tiering pass writes into `provenance.tier_reasons`,
// copied by hand from tiering.py and the implausibility signatures it names;
// the backend's own tests pin that side. What this pins is that every one of
// them has a name here, so none falls through to its raw key on screen.
const SERVER_RULES = [
  'odd_electron',
  'candidate_density',
  'envelope_neighbour',
  'oxygen_lattice',
  'carbon_free',
  'off_calibration',
  'ambiguous_nitrogen',
  'minor_channel',
  'corroborated',
  'no_close_rival',
  'not_measured',
  'inherited_from_owner'
]

describe('tierReasonLabel', () => {
  it('names every rule the server writes, and no other', () => {
    expect(Object.keys(TIER_REASON_LABELS).sort()).toEqual([...SERVER_RULES].sort())
    for (const rule of SERVER_RULES) {
      expect(tierReasonLabel(rule)).toBe(TIER_REASON_LABELS[rule])
      expect(tierReasonLabel(rule)).not.toContain('_')
    }
  })

  // "Satellite" names a signal artifact in this codebase - an FT side lobe -
  // and never an isotopologue, so a reason about one must not use the word.
  it('calls an isotopologue an isotopologue', () => {
    for (const label of Object.values(TIER_REASON_LABELS)) {
      expect(label.toLowerCase()).not.toContain('satellite')
    }
  })

  it('shows a rule it does not know by its key, rather than hiding it', () => {
    expect(tierReasonLabel('series_anchor')).toBe('series anchor')
  })

  it('does not read a label off the object prototype', () => {
    expect(tierReasonLabel('constructor')).toBe('constructor')
    expect(tierReasonLabel('toString')).toBe('toString')
  })

  it('names a missing rule as missing', () => {
    expect(tierReasonLabel(null)).toBe('unnamed rule')
    expect(tierReasonLabel('')).toBe('unnamed rule')
  })
})

describe('tierReasonsOf', () => {
  it('is empty for a row no tiering pass judged', () => {
    expect(tierReasonsOf(null)).toEqual([])
    expect(tierReasonsOf({})).toEqual([])
    expect(tierReasonsOf({ plausibility: 0.9 })).toEqual([])
    expect(tierReasonsOf({ tier_reasons: 'odd_electron' })).toEqual([])
  })

  it('keeps the server sentence and names the rule, in order', () => {
    const reasons = tierReasonsOf({
      tier_reasons: [
        { rule: 'odd_electron', detail: 'C7H7 is an odd-electron neutral', caps: true },
        { rule: 'candidate_density', detail: '3 formulas', caps: true }
      ]
    })
    expect(reasons).toEqual([
      {
        rule: 'odd_electron',
        label: 'radical neutral',
        detail: 'C7H7 is an odd-electron neutral',
        caps: true
      },
      { rule: 'candidate_density', label: 'rivals left standing', detail: '3 formulas', caps: true }
    ])
  })

  // A reason caps only when the server said so: a truthy string or a missing
  // flag would otherwise mark a standing reason as the one that took the tier.
  it('treats only a literal true as capping', () => {
    const reasons = tierReasonsOf({
      tier_reasons: [
        { rule: 'no_close_rival', detail: 'separated', caps: 'false' },
        { rule: 'corroborated', detail: 'two channels' }
      ]
    })
    expect(reasons.map((reason) => reason.caps)).toEqual([false, false])
  })

  it('drops entries that are not reasons, and keeps one with no sentence', () => {
    const reasons = tierReasonsOf({
      tier_reasons: [null, 'odd_electron', { rule: 'not_measured', caps: false }]
    })
    expect(reasons).toEqual([
      { rule: 'not_measured', label: 'not measured', detail: '', caps: false }
    ])
  })
})

describe('reasonIcon', () => {
  it('marks every capping reason alike, an isotopologue taken down with its M0 included', () => {
    expect(reasonIcon({ rule: 'odd_electron', caps: true })).toBe('ph-arrow-down')
    expect(reasonIcon({ rule: 'inherited_from_owner', caps: true })).toBe('ph-arrow-down')
  })

  it('tells what a row kept its tier on from a reason that claims nothing', () => {
    expect(reasonIcon({ rule: 'no_close_rival', caps: false })).toBe('ph-check')
    expect(reasonIcon({ rule: 'not_measured', caps: false })).toBe('ph-minus')
    expect(reasonIcon({ rule: 'inherited_from_owner', caps: false })).toBe(
      'ph-arrow-elbow-down-right'
    )
  })
})

describe('reasonTooltip', () => {
  const CAPS = { rule: 'odd_electron', caps: true }
  const STANDS = { rule: 'no_close_rival', caps: false }

  it('says a capping reason holds a candidate row there', () => {
    expect(reasonTooltip(CAPS, 'candidate')).toBe(
      'Holds this row at candidate - it cannot be assigned while this stands'
    )
  })

  // `caps` is what the rule would take. On a row the evidence already banded
  // lower it took nothing, and saying it "holds" the row would claim a demote
  // that did not happen.
  it('says a capping reason took nothing from a row its evidence put lower', () => {
    expect(reasonTooltip(CAPS, 'below_assignability')).toBe(
      'Would hold this row at candidate, but its evidence already puts it lower'
    )
  })

  it('says a standing reason caps nothing', () => {
    expect(reasonTooltip(STANDS, 'assigned')).toBe(
      'Caps nothing: this row holds the tier its evidence earned'
    )
  })

  it("names the M0 as the row a reason read off an isotopologue's M0 is about", () => {
    const text = reasonTooltip(CAPS, 'candidate', { viaM0: true })
    expect(text).toContain('Holds the M0 at candidate')
    expect(text).toContain('Recorded on the M0, which this isotopologue follows.')
    expect(text).not.toContain('this row')
  })
})
