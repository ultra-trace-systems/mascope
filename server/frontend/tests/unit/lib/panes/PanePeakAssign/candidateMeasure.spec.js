import { describe, it, expect } from 'vitest'

import {
  currentReading,
  measuredLines,
  measuredTierLabel,
  measuredTooltipLines
} from '@/lib/panes/PanePeakAssign/candidateMeasure.js'

const MEASURED = {
  fit_score: 0.81,
  evidence: 0.567,
  tier: 'below_assignability',
  lines: [
    { isotope_label: 'M0', sample_peak_id: 'p-1' },
    { isotope_label: 'M+1', sample_peak_id: null },
    { isotope_label: 'M+2', sample_peak_id: 'p-3' },
    // Fine structure: a second line under the same nominal label.
    { isotope_label: 'M+2', sample_peak_id: 'p-4' }
  ],
  lines_found: 3,
  takes_member: true,
  blocked_reason: null,
  current: { assigned_formula: 'C6H12O6', fit_score: 0.95, evidence: 0.9, tier: 'assigned' }
}

describe('candidateMeasure', () => {
  it('names the tier the way its chip does', () => {
    expect(measuredTierLabel(MEASURED)).toBe('below')
    expect(measuredTierLabel({ tier: null })).toBeNull()
  })

  it('counts the lines the spectrum holds of the ion', () => {
    expect(measuredLines(MEASURED)).toBe('3 of 4 lines')
    expect(measuredLines({ lines: [{ sample_peak_id: 'p' }] })).toBe('1 of 1 line')
    expect(measuredLines({ lines: [] })).toBeNull()
  })

  it('counts found lines off the lines when the count is missing', () => {
    expect(measuredLines({ lines: MEASURED.lines })).toBe('3 of 4 lines')
  })

  it('says what the member reads now', () => {
    expect(currentReading(MEASURED)).toBe('C6H12O6, fit 95%, evidence 90%, assigned')
    expect(currentReading({ current: { assigned_formula: null } })).toBeNull()
    expect(currentReading({ current: { assigned_formula: 'C2H4' } })).toBe('C2H4')
  })

  it('lists the evidence, the tier, the lines and the current reading', () => {
    expect(measuredTooltipLines(MEASURED)).toEqual([
      'evidence: 57%',
      'tier if pinned: Below assignability: a formula was found, but the evidence is too weak to trust',
      'lines in this spectrum: 3 of 4 lines (M0, M+2)',
      'this sample now: C6H12O6, fit 95%, evidence 90%, assigned'
    ])
  })

  it('says why a pin would leave the sample as it is, and no tier', () => {
    const lines = measuredTooltipLines({
      ...MEASURED,
      evidence: null,
      tier: null,
      takes_member: false,
      blocked_reason: 'not reached'
    })
    expect(lines).toContain('if pinned: not reached')
    expect(lines.some((line) => line.startsWith('tier if pinned'))).toBe(false)
    expect(lines.some((line) => line.startsWith('evidence'))).toBe(false)
  })

  it('adds nothing without a measurement', () => {
    expect(measuredTooltipLines(null)).toEqual([])
  })
})
