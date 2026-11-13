import { describe, expect, it } from 'vitest'

import { waterLadderSummary, waterLadderTooltip } from '@/lib/batchWaterLadder'

const segment = (index, verdict, extra = {}) => ({
  segment: index,
  spectra: 12,
  presence: 0.92,
  decoy_presence: 0.04,
  verdict,
  ...extra
})

const RECORD = {
  rung: { core: '[Br]-', ion: '[Br+16xH2O]-', ion_formula: 'BrH32O16-', track: 'M0', waters: 16 },
  segments: [segment(0, 'absent', { presence: 0.2 }), segment(1, 'passed')],
  partition_segments: 2,
  passed_in: [1],
  members_rewritten: 11,
  moved_analyte: true,
  read_before: { formula: 'C13H12O8' },
  reads: { role: 'reagent' },
  files_by_reason: { hand_assigned: 1 }
}

describe('batchWaterLadder', () => {
  it('names the rung and the stretches it passed in', () => {
    expect(waterLadderSummary(RECORD)).toBe(
      '[Br+16xH2O]-: a water-ladder rung in 1 of 2 stretches of acquisition'
    )
    expect(waterLadderSummary(null)).toBe('')
  })

  it('counts every stretch of the batch, not only the ones the ladder reached', () => {
    // Four stretches; the core shows in one, where the rung passed.
    const record = {
      ...RECORD,
      segments: [segment(2, 'passed')],
      passed_in: [2],
      partition_segments: 4
    }
    expect(waterLadderSummary(record)).toBe(
      '[Br+16xH2O]-: a water-ladder rung in 1 of 4 stretches of acquisition'
    )
    const single = { ...record, partition_segments: 1 }
    expect(waterLadderSummary(single)).toMatch(/in 1 of 1 stretch of acquisition$/)
  })

  it("names a heavier line's track beside its ion", () => {
    const record = { ...RECORD, rung: { ...RECORD.rung, track: '81Br' } }
    expect(waterLadderSummary(record)).toMatch(/^\[Br\+16xH2O\]- \(81Br line\): /)
  })

  it('says where it passed, what was claimed and what it read before', () => {
    expect(waterLadderTooltip(RECORD)).toBe(
      '[Br+16xH2O]-: a water-ladder rung in 1 of 2 stretches of acquisition. ' +
        'Stretch 2: in 92 % of its 12 samples, 4 % at the masses beside it. ' +
        'Claimed in 11 more samples. ' +
        'The batch peak read C13H12O8 before. ' +
        'Kept as they read: 1 sample assigned by hand.'
    )
    expect(waterLadderTooltip(undefined)).toBe('')
  })

  it("says when the batch peak's other samples keep its reading", () => {
    const record = {
      ...RECORD,
      moved_analyte: false,
      reads: { role: null },
      files_by_reason: {},
      members_rewritten: 1
    }
    expect(waterLadderTooltip(record)).toMatch(/Claimed in 1 more sample\. /)
    expect(waterLadderTooltip(record)).toMatch(
      /Its other samples' readings keep the batch peak's own\.$/
    )
  })

  it('says why a curated batch peak was not claimed', () => {
    const record = { ...RECORD, held: 'curated', members_rewritten: 0, files_by_reason: {} }
    expect(waterLadderTooltip(record)).toMatch(/Not claimed: the batch peak is curated by hand\./)
    expect(waterLadderTooltip(record)).not.toMatch(/Claimed in/)
  })

  it('names every reason a sample is kept', () => {
    const record = {
      ...RECORD,
      files_by_reason: {
        target_library: 2,
        listed: 1,
        line_of_another: 3,
        claimed_otherwise: 1,
        carries_lines: 1,
        no_monoisotopic: 2
      }
    }
    expect(waterLadderTooltip(record)).toMatch(
      /Kept as they read: 2 samples named by the target library, 1 sample named by a reference list, 3 samples another ion's isotope line, 1 sample another source ion's claim, 1 sample a reading with an isotope line the hydrate cannot have, 2 samples a heavier line whose hydrate's own peak reads otherwise\.$/
    )
  })
})
