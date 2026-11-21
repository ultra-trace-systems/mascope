import { describe, it, expect } from 'vitest'

import {
  DEFAULT_TIER_BANDS,
  bandMissed,
  bandsOf,
  barPosition,
  batchTile,
  formatShare,
  isotopeTile,
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
    expect(memberLabel({ step: -1 }, SERIES)).toBe('-1 CH2')
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
      found: 2,
      predicted: 4
    })
  })

  it('says a lone M0 is all there is', () => {
    expect(isotopeTile({ family: [{}], missing: [], abundanceError: null }).value).toBe('M0 only')
  })
})

describe('batchTile', () => {
  it('names the samples the batch peak is in, and its readings', () => {
    expect(batchTile({ present: 7, total: 9, readings: 2, inBatch: true })).toEqual({
      value: '7 of 9',
      caption: 'samples it is in · 2 batch readings'
    })
    expect(batchTile({ present: 1, total: null, readings: 1, inBatch: true })).toEqual({
      value: '1',
      caption: 'sample it is in · 1 batch reading'
    })
  })

  it('says where the count is unknown, and where there is no batch peak', () => {
    expect(batchTile({ present: null, total: null, readings: 3, inBatch: true }).caption).toBe(
      '3 batch readings'
    )
    expect(batchTile({ present: null, total: null, readings: 0, inBatch: false })).toEqual({
      value: '—',
      caption: 'in no batch peak'
    })
  })
})
