import { describe, it, expect } from 'vitest'

import {
  LEDGER_CONFIDENCE_TOOLTIP,
  LEDGER_P_CORRECT_TOOLTIP,
  P_CORRECT_TOOLTIP,
  UNCALIBRATED_REASONS,
  uncalibratedReason
} from '@/lib/pCorrect'

const row = (extra = {}) => ({ assigned_formula: 'C6H12O6', source: 'database', ...extra })

describe('uncalibratedReason', () => {
  // A demoted satellite is 'manual' and formula-less at once; the missing
  // formula is the reason, or the row calls itself hand-assigned while its tier
  // chip reads Unassigned.
  it('puts a missing formula before the source', () => {
    expect(uncalibratedReason({ assigned_formula: null, source: 'manual' })).toBe(
      UNCALIBRATED_REASONS.unassigned
    )
    expect(uncalibratedReason(null)).toBe(UNCALIBRATED_REASONS.unassigned)
  })

  it('names the source when the source is the reason', () => {
    expect(uncalibratedReason(row({ source: 'manual' }))).toBe(UNCALIBRATED_REASONS.manual)
    expect(uncalibratedReason(row({ source: 'untargeted' }))).toBe(UNCALIBRATED_REASONS.untargeted)
  })

  // The ledger does not score a sample, so on a row it serves the instrument's
  // calibration is not the thing to blame; the row's own reasons still win.
  it("blames the instrument's calibration only on a run's own row", () => {
    expect(uncalibratedReason(row())).toBe(UNCALIBRATED_REASONS.uncalibrated)
    expect(uncalibratedReason(row(), { fromLedger: true })).toBe(UNCALIBRATED_REASONS.ledger)
    expect(uncalibratedReason(row({ source: 'untargeted' }), { fromLedger: true })).toBe(
      UNCALIBRATED_REASONS.untargeted
    )
  })

  it('has one distinct sentence per reason', () => {
    const reasons = Object.values(UNCALIBRATED_REASONS)
    expect(new Set(reasons).size).toBe(reasons.length)
  })
})

describe('P(correct) tooltips', () => {
  it('keeps the header to one line, with none of the reasons in it', () => {
    expect(P_CORRECT_TOOLTIP).toBe('Calibrated probability the assignment is correct')
    for (const reason of Object.values(UNCALIBRATED_REASONS)) {
      expect(P_CORRECT_TOOLTIP).not.toContain(reason)
    }
  })

  it('says where a ledger-served value and a missing confidence come from', () => {
    expect(LEDGER_P_CORRECT_TOOLTIP).toMatch(/batch ledger/)
    expect(LEDGER_P_CORRECT_TOOLTIP).toMatch(/folded in/)
    expect(LEDGER_CONFIDENCE_TOOLTIP).toMatch(/assignment run/)
    expect(LEDGER_CONFIDENCE_TOOLTIP).toMatch(/batch ledger/)
  })
})
