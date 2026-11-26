import { describe, it, expect } from 'vitest'

import {
  CAPPING_RULES,
  TIER_REASON_LABELS,
  cappingRulesOf,
  holdsTierDown,
  reasonFigure,
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
  'evidence_band',
  'odd_electron',
  'oxygen_free_cluster',
  'polyhalide_cluster',
  'candidate_density',
  'lone_peak',
  'context_prior',
  'nox_skeleton',
  // A batch peak's rule (batch_peaks.hold_persistence_only), in the same
  // vocabulary.
  'persistence_only',
  'absent_line',
  'unresolved_peak',
  'envelope_neighbour',
  'oxygen_lattice',
  'carbon_free',
  'off_calibration',
  'ambiguous_nitrogen',
  'ambiguous_adduct',
  'minor_channel',
  'corroborated',
  'second_line',
  'series_anchor',
  'on_a_list',
  'same_ion_settled',
  'no_close_rival',
  'not_measured',
  'in_source_fragment',
  'unread_line',
  'inherited_from_owner',
  'envelope_claim',
  'isotopologue_in_doubt',
  'isotopologue_untracked'
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
    expect(tierReasonLabel('flat_background')).toBe('flat background')
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

  it('carries the neighbouring peak a reason names', () => {
    const [reason] = tierReasonsOf({
      tier_reasons: [
        {
          rule: 'unresolved_peak',
          detail: 'its peak sits 0.60 widths from a peak at m/z 181.0924',
          caps: true,
          neighbour_peak_id: 1234,
          neighbour_mz: 181.0924
        }
      ]
    })
    expect(reason.label).toBe('peak not separated')
    expect(reason.neighbour).toEqual({ peakId: '1234', mz: 181.0924 })
  })

  it('names no neighbour where the reason does not say which and where', () => {
    const reasons = tierReasonsOf({
      tier_reasons: [
        { rule: 'unresolved_peak', caps: true, neighbour_peak_id: 'p-1' },
        { rule: 'unresolved_peak', caps: true, neighbour_mz: 181.09 },
        { rule: 'unresolved_peak', caps: true, neighbour_peak_id: '', neighbour_mz: 181.09 },
        { rule: 'unresolved_peak', caps: true, neighbour_peak_id: 'p-1', neighbour_mz: NaN },
        { rule: 'unresolved_peak', caps: true, neighbour_peak_id: {}, neighbour_mz: 181.09 }
      ]
    })
    expect(reasons.map((reason) => 'neighbour' in reason)).toEqual([
      false,
      false,
      false,
      false,
      false
    ])
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

  // A grouping is neither what took the tier nor what the row kept it on, so
  // it wears neither the arrow nor the check.
  it('marks a grouping the run records apart from both', () => {
    expect(reasonIcon({ rule: 'in_source_fragment', caps: false })).toBe('ph-info')
    expect(reasonIcon({ rule: 'unread_line', caps: false })).toBe('ph-info')
    expect(holdsTierDown({ rule: 'unread_line', caps: false })).toBe(false)
    expect(holdsTierDown({ rule: 'in_source_fragment', caps: false })).toBe(false)
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

  it('says a grouping neither caps nor keeps the tier', () => {
    const grouping = { rule: 'in_source_fragment', caps: false }
    for (const tier of ['assigned', 'candidate']) {
      expect(reasonTooltip(grouping, tier)).toBe(
        'Caps nothing and keeps nothing: a grouping the run records about this row'
      )
    }
  })

  it('says a line that could not be read is no evidence against the row', () => {
    const unread = { rule: 'unread_line', caps: false }
    expect(reasonTooltip(unread, 'assigned')).toBe(
      'Caps nothing: a line the file could not show apart is no evidence against this row'
    )
  })

  // The band is where the row's evidence put it before any rule, so it does
  // not "hold" or "would hold" anything: it is the floor the rules lower from.
  it('says the evidence band sets the tier, whatever the tier is', () => {
    // It never caps: no rule took anything. It is still why the row is low.
    const band = { rule: 'evidence_band', caps: false }
    for (const tier of ['candidate', 'below_assignability']) {
      expect(reasonTooltip(band, tier)).toBe(
        "Sets this row's tier: the reasons below can only lower it further"
      )
    }
    expect(reasonIcon(band)).toBe('ph-arrow-down')
    expect(holdsTierDown(band)).toBe(true)
    expect(holdsTierDown({ rule: 'corroborated', caps: false })).toBe(false)
    expect(holdsTierDown({ rule: 'odd_electron', caps: true })).toBe(true)
  })

  it("names the M0 as the row a reason read off an isotopologue's M0 is about", () => {
    const text = reasonTooltip(CAPS, 'candidate', { viaM0: true })
    expect(text).toContain('Holds the M0 at candidate')
    expect(text).toContain('Recorded on the M0, which this isotopologue follows.')
    expect(text).not.toContain('this row')
  })
})

// The number or formula a reason turns on, beside the rule's name on the card's
// one line; the sentence is a click away (docs/dev/assignment_ledger_and_card.md,
// section 4.1).
describe('reasonFigure', () => {
  const figure = (detail) => reasonFigure({ rule: 'x', detail })

  it('reads the share a sentence names first', () => {
    expect(figure('evidence 30% (fit 30% x plausibility 100%) is under the candidate band')).toBe(
      '30%'
    )
    expect(figure('an abundance 12.5 % off its prediction')).toBe('12.5%')
  })

  it('reads the widths an off-calibration row sits off', () => {
    expect(figure("mass error sits 4.2 widths off the run's own fitted centre")).toBe('4.2 widths')
  })

  it("reads the widths of an unresolved peak, not its neighbour's height", () => {
    // The server's sentence (tiering.unresolved_peak_reason) names both; the
    // distance decides the reason. A taller neighbour's sentence has no share.
    const unresolved = (detail) => reasonFigure({ rule: 'unresolved_peak', detail })
    expect(
      unresolved(
        'its peak sits 0.80 widths from a peak at m/z 301.1234 at 45.0% of its height, ' +
          'short of the 1.10 widths two lines of these heights need to show as two peaks'
      )
    ).toBe('0.80 widths')
    expect(
      unresolved(
        'its peak sits 0.65 widths from a peak at m/z 301.1234 1.5 times as tall, ' +
          'short of the 1.20 widths two lines of these heights need to show as two peaks'
      )
    ).toBe('0.65 widths')
  })

  it('reads a count the sentence opens on', () => {
    expect(figure("3 formulas this peak's evidence could not separate")).toBe('3 formulas')
  })

  it('reads a formula, never a capitalised word', () => {
    expect(figure('C3H4O4 is an odd-electron neutral - a radical rather than a molecule')).toBe(
      'C3H4O4'
    )
    expect(figure('It names no number at all')).toBe('')
    expect(figure('the NOx skeleton of its groups')).toBe('')
    expect(figure('one peak is all that saw Br2Cl: a lone line')).toBe('Br2Cl')
  })

  it('names nothing for a reason with no sentence', () => {
    expect(reasonFigure({ rule: 'x' })).toBe('')
    expect(reasonFigure(null)).toBe('')
  })
})

// The ledger's slim rows carry their reasons' rules alone, no `caps`: the
// capping rules are named here, and the tier chip's mark reads them.
describe('cappingRulesOf', () => {
  it('names the capping rules a row records, in its order, and nothing else', () => {
    expect(
      cappingRulesOf(['evidence_band', 'lone_peak', 'corroborated', 'off_calibration'])
    ).toEqual([
      { rule: 'lone_peak', label: 'one peak only' },
      { rule: 'off_calibration', label: 'off calibration' }
    ])
    expect(cappingRulesOf(['second_line', 'not_measured', 'inherited_from_owner'])).toEqual([])
    // An isotopologue's own caps, which its ledger chip marks too.
    expect(
      cappingRulesOf(['isotopologue_in_doubt', 'envelope_claim', 'isotopologue_untracked'])
    ).toEqual([
      { rule: 'isotopologue_in_doubt', label: 'line in doubt' },
      { rule: 'envelope_claim', label: "read as a neighbour's line" },
      { rule: 'isotopologue_untracked', label: 'off its M0' }
    ])
    expect(cappingRulesOf(null)).toEqual([])
    expect(cappingRulesOf('lone_peak')).toEqual([])
  })

  it('names only rules the build has a label for', () => {
    for (const rule of CAPPING_RULES) expect(TIER_REASON_LABELS).toHaveProperty(rule)
  })
})
