import { describe, it, expect } from 'vitest'

import {
  DEFAULT_TIER_BANDS,
  bandMissed,
  bandsOf,
  barPosition,
  batchTile,
  EMPTY_REASONS,
  candidateHold,
  formatShare,
  holdCap,
  isotopeTile,
  M0_ABUNDANCE_TOOLTIP,
  memberLabel,
  orderedReasons,
  reasonGroup,
  reasonTone,
  shareDigits,
  whyTitle
} from '@/lib/panes/PanePeakAssign/card/cardReadings.js'

// What the inspector card reads off a row, its run and its detail, as plain
// functions (docs/dev/assignment_ledger_and_card.md, section 4.1).

describe('bandsOf', () => {
  it("reads the run's own bands", () => {
    expect(bandsOf({ candidate: 0.5, assigned: 0.8 })).toEqual({
      candidate: 0.5,
      assigned: 0.8,
      own: true
    })
  })

  it("falls back to the engine's defaults, saying so", () => {
    for (const bands of [null, {}, { candidate: 0.5 }, { candidate: '0.5', assigned: 0.8 }]) {
      expect(bandsOf(bands)).toEqual({ ...DEFAULT_TIER_BANDS, own: false })
    }
    expect(DEFAULT_TIER_BANDS).toEqual({ candidate: 0.45, assigned: 0.75 })
  })

  it('places a share on the bar, inside it', () => {
    expect(barPosition(0.3)).toBe(30)
    expect(barPosition(1.4)).toBe(100)
    expect(barPosition(-0.1)).toBe(0)
  })
})

describe('whyTitle', () => {
  it("titles the box with the tier's short label", () => {
    expect(whyTitle('assigned')).toBe('Why assigned')
    expect(whyTitle('candidate')).toBe('Why candidate')
    expect(whyTitle('below_assignability')).toBe('Why below')
    expect(whyTitle('unassigned')).toBe('Why unassigned')
    expect(whyTitle(null)).toBe('Why unassigned')
  })
})

describe('the reasons', () => {
  const BAND = { rule: 'evidence_band', caps: false }
  const CAP = { rule: 'lone_peak', caps: true }
  const KEPT = { rule: 'corroborated', caps: false }
  const NONE = { rule: 'not_measured', caps: false }

  it('puts what holds the tier back first, each group in the order written', () => {
    expect(orderedReasons([KEPT, BAND, NONE, CAP])).toEqual([BAND, CAP, KEPT, NONE])
    expect(orderedReasons(null)).toEqual([])
  })

  it('gives each what it does to the tier', () => {
    expect([BAND, CAP, KEPT, NONE].map(reasonTone)).toEqual([
      'holds-back',
      'holds-back',
      'supports',
      'neutral'
    ])
  })
})

// What holds a candidate row at candidate, for the Why box's first line and
// the tier chip's hover (MAS-1296).
describe('candidateHold', () => {
  const BANDS = { candidate: 0.45, assigned: 0.75 }
  const BAND = { rule: 'evidence_band', label: 'evidence band', detail: '', caps: false }
  const LONE = { rule: 'lone_peak', label: 'one peak only', detail: 'one peak', caps: true }
  const RIVALS = {
    rule: 'candidate_density',
    label: 'rivals left standing',
    detail: "3 formulas this peak's evidence could not separate",
    caps: true
  }
  const RADICAL = {
    rule: 'odd_electron',
    label: 'radical neutral',
    detail: 'C10H11 is an odd-electron neutral',
    caps: true
  }
  const KEPT = { rule: 'no_close_rival', label: 'no close rival', detail: '', caps: false }
  const follows = (caps) => ({ rule: 'inherited_from_owner', label: 'follows its M0', caps })
  const hold = (options) => candidateHold({ tier: 'candidate', bands: BANDS, ...options })

  it('names every rule that holds the row, with the measure it turned on', () => {
    expect(hold({ reasons: [KEPT, LONE, RIVALS], evidence: 0.9 })).toEqual({
      text: 'Held at candidate: one peak only, rivals left standing (3 formulas)',
      caps: [LONE, RIVALS],
      viaM0: false,
      m0Capped: false,
      band: false
    })
  })

  it("leaves out a formula the sentence names, which is the row's own", () => {
    expect(hold({ reasons: [RADICAL], evidence: 0.9 }).text).toBe(
      'Held at candidate: radical neutral'
    )
  })

  it('leads with the band where the evidence is under the assigned one, then the rules', () => {
    const result = hold({ reasons: [BAND, LONE], evidence: 0.6 })
    expect(result.text).toBe(
      'Held at candidate: evidence 60% is under the assigned band of 75%; also one peak only'
    )
    expect(result.band).toBe(true)
  })

  it('writes the evidence finely enough not to read as the band', () => {
    expect(hold({ reasons: [BAND], evidence: 0.746 }).text).toBe(
      'Held at candidate: evidence 74.6% is under the assigned band of 75.0%'
    )
  })

  // A candidate row under the candidate band was not held there by the band:
  // something put it up, and the line must not say the band held it.
  it('does not name the assigned band for evidence under the candidate one', () => {
    expect(hold({ reasons: [BAND], evidence: 0.3 }).band).toBe(false)
  })

  it("reads an isotopologue's hold off its M0", () => {
    const result = hold({
      reasons: [follows(true)],
      ownerReasons: [BAND, LONE],
      ownerEvidence: 0.9
    })
    expect(result.text).toBe('Held at candidate with its M0: one peak only')
    expect(result.viaM0).toBe(true)
    // Before the M0's detail arrives.
    expect(hold({ reasons: [follows(true)] }).text).toBe('Held at candidate with its M0')
  })

  // The run marks the inherited reason as capping only where a rule capped
  // the M0 (tiering.py): an M0 its band alone holds still holds its
  // isotopologue, and the line says so with the M0's evidence.
  it("names the M0's band for an isotopologue its M0's band alone holds", () => {
    const result = hold({
      reasons: [follows(false)],
      ownerReasons: [BAND, KEPT],
      evidence: 0.2,
      ownerEvidence: 0.749
    })
    expect(result.text).toBe(
      'Held at candidate with its M0: evidence 74.9% is under the assigned band of 75.0%'
    )
    expect(result).toMatchObject({ viaM0: true, m0Capped: false, band: true, caps: [] })
  })

  it("names the M0's band beside the rule that capped it", () => {
    expect(
      hold({ reasons: [follows(true)], ownerReasons: [BAND, LONE], ownerEvidence: 0.6 }).text
    ).toBe(
      'Held at candidate with its M0: evidence 60% is under the assigned band of 75%; also one peak only'
    )
  })

  it("says no reason is recorded where the isotopologue's M0 names none", () => {
    expect(hold({ reasons: [follows(false)], ownerReasons: [KEPT] }).text).toBe(
      'Held at candidate with its M0: no reason recorded - the run names no rule that holds the M0'
    )
  })

  it("says a row stands on the batch's decision rather than held", () => {
    const decided = { rule: 'batch_known_species', label: 'batch known species', caps: false }
    expect(hold({ reasons: [BAND, decided], evidence: 0.3 }).text).toMatch(
      /^At candidate on the batch's decision/
    )
  })

  it('says no reason is recorded where the judged row names none', () => {
    expect(hold({ reasons: [KEPT], evidence: 0.9 }).text).toBe(
      'Held at candidate: no reason recorded - the run names no rule that holds this row'
    )
  })

  it('is null off candidate, and on a row no pass judged', () => {
    expect(hold({ tier: 'assigned', reasons: [LONE] })).toBeNull()
    expect(hold({ tier: 'below_assignability', reasons: [LONE] })).toBeNull()
    expect(hold({ reasons: [] })).toBeNull()
    expect(hold({ reasons: null })).toBeNull()
  })

  it('gives the tier chip a cap mark for a rule, none for the band alone', () => {
    expect(holdCap(hold({ reasons: [LONE, RIVALS], evidence: 0.9 }))).toEqual({
      label: 'Held down by: one peak only, rivals left standing',
      tooltip: 'Held at candidate: one peak only, rivals left standing (3 formulas)'
    })
    expect(holdCap(hold({ reasons: [follows(true)] }))).toEqual({
      label: 'Held down with its M0',
      tooltip: 'Held at candidate with its M0'
    })
    expect(holdCap(hold({ reasons: [BAND], evidence: 0.6 }))).toBeNull()
    expect(
      holdCap(hold({ reasons: [follows(false)], ownerReasons: [BAND], ownerEvidence: 0.6 }))
    ).toBeNull()
    expect(holdCap(null)).toBeNull()
  })
})

describe('the evidence against the band it misses', () => {
  const BANDS = { candidate: 0.45, assigned: 0.75 }

  it('finds the edge of the band the evidence falls short of', () => {
    expect(bandMissed(0.746, BANDS)).toBe(0.75)
    expect(bandMissed(0.45, BANDS)).toBe(0.75)
    expect(bandMissed(0.3, BANDS)).toBe(0.45)
    expect(bandMissed(0.75, BANDS)).toBeNull()
    expect(bandMissed(null, BANDS)).toBeNull()
  })

  it('takes as many decimals as it needs not to read as the band (tiering._digits)', () => {
    expect(shareDigits(0.3, 0.45)).toBe(0)
    expect(shareDigits(0.746, 0.75)).toBe(1)
    expect(shareDigits(0.7496, 0.75)).toBe(2)
    expect(shareDigits(0.9, null)).toBe(0)
    expect(formatShare(0.746, 1)).toBe('74.6%')
    expect(formatShare(0.3)).toBe('30%')
    expect(formatShare(null)).toBe('-')
  })
})

describe('reasonGroup', () => {
  const CHANNELS = { kind: 'channels', members: [{ batch_peak_id: 'a' }] }
  const SERIES = { kind: 'series', unit: 'CH2', members: [{ batch_peak_id: 'b' }] }

  it('finds the group a second channel or a series spans', () => {
    expect(reasonGroup({ rule: 'corroborated' }, [SERIES, CHANNELS])).toBe(CHANNELS)
    expect(reasonGroup({ rule: 'series_anchor' }, [CHANNELS, SERIES])).toBe(SERIES)
  })

  it('finds none for another reason, an empty group or no groups', () => {
    expect(reasonGroup({ rule: 'lone_peak' }, [CHANNELS])).toBeNull()
    expect(reasonGroup({ rule: 'corroborated' }, [{ kind: 'channels', members: [] }])).toBeNull()
    expect(reasonGroup({ rule: 'corroborated' }, null)).toBeNull()
  })

  // A peak's formula lattice can reach several series; the reason is about
  // the row's own, the detail's `batch_series`.
  describe('where the peak sits in several series', () => {
    const member = [{ batch_peak_id: 'b' }]
    const O_SERIES = {
      kind: 'series',
      unit: 'O',
      root_batch_peak_id: 'r1',
      ionization_mechanism_id: 'm1',
      members: member
    }
    const CH2_SERIES = {
      kind: 'series',
      unit: 'CH2',
      root_batch_peak_id: 'r2',
      ionization_mechanism_id: 'm1',
      members: member
    }
    const CH2_OTHER_MECHANISM = { ...CH2_SERIES, ionization_mechanism_id: 'm2' }
    const series = (record) => reasonGroup({ rule: 'series_anchor' }, groups, record)
    const groups = [O_SERIES, CH2_OTHER_MECHANISM, CH2_SERIES]

    it("finds the row's own by unit and mechanism", () => {
      expect(series({ unit: 'CH2', root_batch_peak_id: 'r2', ionization_mechanism_id: 'm1' })).toBe(
        CH2_SERIES
      )
      expect(series({ unit: 'O', root_batch_peak_id: 'r1', ionization_mechanism_id: 'm1' })).toBe(
        O_SERIES
      )
      expect(series({ unit: 'CH2', root_batch_peak_id: 'r2', ionization_mechanism_id: 'm2' })).toBe(
        CH2_OTHER_MECHANISM
      )
    })

    // The batch serves one group per lattice, rooted at its lightest root
    // (`batch_series._distinct`): a record naming the lattice's second root
    // still reads that group.
    it("finds the lattice's group where the record names another root of it", () => {
      const LATTICE = {
        kind: 'series',
        unit: 'CH2',
        root_batch_peak_id: 'R1',
        ionization_mechanism_id: 'm1',
        members: [
          { batch_peak_id: 'R1', step: 0 },
          { batch_peak_id: 'A', step: 1 },
          { batch_peak_id: 'R2', step: 3 },
          { batch_peak_id: 'B', step: 4 }
        ]
      }
      const record = { unit: 'CH2', root_batch_peak_id: 'R2', ionization_mechanism_id: 'm1' }
      expect(reasonGroup({ rule: 'series_anchor' }, [O_SERIES, LATTICE], record)).toBe(LATTICE)
    })

    it('finds none where the batch serves no group of that series', () => {
      expect(series({ unit: 'H2O', root_batch_peak_id: 'r2' })).toBeNull()
      expect(series({ unit: 'O', ionization_mechanism_id: 'm2' })).toBeNull()
    })

    it("guesses none among several without the row's series", () => {
      expect(series(null)).toBeNull()
      expect(reasonGroup({ rule: 'series_anchor' }, [CHANNELS, O_SERIES], null)).toBe(O_SERIES)
    })
  })

  it("labels a series member by its step in the group's unit, another by its channel", () => {
    expect(memberLabel({ step: 0 }, SERIES)).toBe('root')
    expect(memberLabel({ step: 2 }, SERIES)).toBe('+2 CH2')
    expect(memberLabel({ step: -1 }, SERIES)).toBe('\u22121 CH2')
    expect(memberLabel({ step: 1 }, { ...SERIES, unit: 'O' })).toBe('+1 O')
    expect(memberLabel({ mechanism: '[M+NO3]-', formula: 'X' }, CHANNELS)).toBe('[M+NO3]-')
    expect(memberLabel({ formula: 'C3H4O4' }, CHANNELS)).toBe('C3H4O4')
  })
})

describe('isotopeTile', () => {
  it('counts the lines found of those predicted, with the abundance error', () => {
    expect(
      isotopeTile({
        family: [{}, {}, {}],
        missing: [{ state: 'absent' }, { state: 'untestable' }],
        abundanceError: -0.031
      })
    ).toEqual({
      value: '2 of 4',
      caption: 'abundance off by 3.10% · 1 not found · 1 not tested',
      captionTooltip: null,
      tooltip: '2 of 4 predicted isotope lines found',
      found: 2,
      predicted: 4
    })
    expect(isotopeTile({ family: [{}], missing: [{ state: 'absent' }] }).tooltip).toBe(
      '0 of 1 predicted isotope line found'
    )
  })

  it("reads the M0's abundance error as a dash: the other lines are measured against it", () => {
    const tile = isotopeTile({
      family: [{}, {}],
      missing: [],
      abundanceError: 0,
      reference: true
    })
    expect(tile.caption).toBe('abundance error -')
    expect(tile.captionTooltip).toBe(M0_ABUNDANCE_TOOLTIP)
    expect(tile.captionTooltip).toContain('the other lines are measured against')
  })

  it('keeps a zero on any other line, whose error really is zero', () => {
    const tile = isotopeTile({ family: [{}, {}], missing: [], abundanceError: 0 })
    expect(tile.caption).toBe('abundance off by 0.00%')
    expect(tile.captionTooltip).toBeNull()
  })

  it('says a lone M0 is all there is', () => {
    const tile = isotopeTile({ family: [{}], missing: [], abundanceError: null })
    expect(tile.value).toBe('M0 only')
    expect(tile.tooltip).toMatch(/only the main peak, M0, is measured/)
  })
})

describe('isotopeTile on a run that recorded what it asked', () => {
  const asked = [{ state: 'found' }, { state: 'found' }, { state: 'absent' }]

  it('counts the lines found of those asked, and says the lines under the gate were not asked', () => {
    const tile = isotopeTile({
      family: [{}, {}, {}],
      missing: [asked[2]],
      abundanceError: null,
      asked,
      asksShare: 0.03
    })
    expect(tile.value).toBe('2 of 3')
    expect(tile.caption).toBe('1 not found')
    expect(tile.tooltip).toBe(
      '2 of 3 isotope lines the run asked for found\n' +
        'Lines predicted under 3% of M0 were not asked: this peak is too faint to show them. ' +
        'The run does not record how many there are.'
    )
  })

  it('counts a line found that the run did not ask for apart', () => {
    const tile = isotopeTile({
      family: [{}, {}, {}, {}],
      missing: [asked[2]],
      abundanceError: null,
      asked,
      asksShare: 0.03,
      unasked: 1
    })
    expect(tile.value).toBe('2 of 3')
    expect(tile.caption).toBe('1 not found · 1 found, not asked')
    expect(tile.tooltip).toContain('1 more line was found that the run did not ask for.')
  })

  it('says a lone M0 had nothing asked, and from what share', () => {
    const tile = isotopeTile({
      family: [{}],
      missing: [],
      abundanceError: 0,
      reference: true,
      asked: [],
      asksShare: 0.375
    })
    expect(tile.value).toBe('M0 only')
    expect(tile.caption).toBe('none asked: all under 37.5% of M0')
    expect(tile.tooltip).toMatch(
      /^No isotope line was asked for\. Lines predicted under 37\.5% of M0/
    )
    expect(
      isotopeTile({ family: [{}, {}], missing: [], asked: [], asksShare: 0.375, unasked: 1 })
        .caption
    ).toBe('none asked: all under 37.5% of M0 · 1 found')
  })
})

describe('batchTile', () => {
  const lines = (tile) => tile.lines.map(({ label, value }) => [label, value])

  it('lays the samples and the readings out as two labelled lines', () => {
    const tile = batchTile({ present: 7, total: 9, readings: 2, inBatch: true })
    expect(lines(tile)).toEqual([
      ['Samples it is in', '7 of 9'],
      ['Batch readings', '2']
    ])
    expect(tile.lines[0].tooltip).toBe('The batch peak is in 7 of 9 samples')
    expect(tile.lines[1].tooltip).toBe(
      '2 readings of the batch stages, listed under Across the batch'
    )
    const one = batchTile({ present: 1, total: null, readings: 1, inBatch: true })
    expect(lines(one)).toEqual([
      ['Samples it is in', '1'],
      ['Batch readings', '1']
    ])
    expect(one.lines[0].tooltip).toBe('The batch peak is in 1 sample')
    expect(one.lines[1].tooltip).toMatch(/^1 reading of/)
  })

  it('takes a counted batch peak as one, whatever else the detail serves', () => {
    expect(lines(batchTile({ present: 3, total: 4, readings: 0, inBatch: false }))).toEqual([
      ['Samples it is in', '3 of 4'],
      ['Batch readings', '0']
    ])
  })

  it('gives the reason where the count is unknown, and where there is no batch peak', () => {
    const uncounted = batchTile({ present: null, total: null, readings: 3, inBatch: true })
    expect(lines(uncounted)).toEqual([
      ['Samples it is in', '—'],
      ['Batch readings', '3']
    ])
    expect(uncounted.lines[0].tooltip).toBe(EMPTY_REASONS.samples)
    expect(
      batchTile({ present: null, total: null, readings: 0, inBatch: true }).lines
    ).toHaveLength(2)
    expect(batchTile({ present: null, total: null, readings: 0, inBatch: false })).toEqual({
      lines: null,
      value: '—',
      caption: 'in no batch peak',
      tooltip: EMPTY_REASONS.noBatchPeak
    })
  })
})
