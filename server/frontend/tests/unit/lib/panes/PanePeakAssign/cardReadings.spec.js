import { describe, it, expect } from 'vitest'

import {
  DEFAULT_TIER_BANDS,
  bandsOf,
  barPosition,
  batchTile,
  isotopeTile,
  memberLabel,
  orderedReasons,
  reasonGroup,
  reasonTone,
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

describe('reasonGroup', () => {
  const CHANNELS = { kind: 'channels', members: [{ batch_peak_id: 'a' }] }
  const SERIES = { kind: 'series', members: [{ batch_peak_id: 'b' }] }

  it('finds the group a second channel or a series spans', () => {
    expect(reasonGroup({ rule: 'corroborated' }, [SERIES, CHANNELS])).toBe(CHANNELS)
    expect(reasonGroup({ rule: 'series_anchor' }, [CHANNELS, SERIES])).toBe(SERIES)
  })

  it('finds none for another reason, an empty group or no groups', () => {
    expect(reasonGroup({ rule: 'lone_peak' }, [CHANNELS])).toBeNull()
    expect(reasonGroup({ rule: 'corroborated' }, [{ kind: 'channels', members: [] }])).toBeNull()
    expect(reasonGroup({ rule: 'corroborated' }, null)).toBeNull()
  })

  it('labels a series member by its step and another by its channel', () => {
    const series = { unit: 'CH2' }
    expect(memberLabel({ step: 0 }, SERIES, series)).toBe('root')
    expect(memberLabel({ step: 2 }, SERIES, series)).toBe('+2 CH2')
    expect(memberLabel({ step: -1 }, SERIES, series)).toBe('-1 CH2')
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
