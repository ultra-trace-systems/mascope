import { describe, it, expect } from 'vitest'

import {
  askedLineOf,
  askedNumbers,
  envelopeGate,
  noneAskedSummary,
  notAskedNote,
  notFoundLines,
  notFoundTooltip,
  notFoundWord,
  predictedLines
} from '@/lib/predictedEnvelope'

// The lines a monoisotopic row's run asked the spectrum for, as the tiering
// pass records them in `provenance.predicted_envelope` (step 3.4e).

const line = (state, extra = {}) => ({
  line: '81Br',
  predicted_mz: 260.98023,
  predicted_share: 0.9728,
  state,
  ...extra
})

describe('predictedLines', () => {
  it('reads the lines a row was asked for, in order', () => {
    const lines = [line('found', { line: '13C' }), line('absent')]
    expect(predictedLines({ predicted_envelope: { lines } })).toEqual(lines)
  })

  it('reads none from a row no run asked', () => {
    expect(predictedLines(null)).toEqual([])
    expect(predictedLines({})).toEqual([])
    expect(predictedLines({ predicted_envelope: { lines: 'no' } })).toEqual([])
  })

  it('skips an entry with no predicted m/z or share', () => {
    const lines = [
      null,
      { state: 'absent' },
      line('absent', { predicted_mz: null }),
      line('absent', { predicted_share: null })
    ]
    expect(predictedLines({ predicted_envelope: { lines } })).toEqual([])
  })
})

describe('notFoundLines', () => {
  it('keeps the absent and untestable lines, not the found ones', () => {
    const absent = line('absent')
    const untestable = line('untestable', { line: '13C', untestable: 'artifact' })
    const provenance = { predicted_envelope: { lines: [line('found'), absent, untestable] } }
    expect(notFoundLines(provenance)).toEqual([absent, untestable])
  })
})

describe('what a line not found says', () => {
  it('is not found where nothing was there, and not tested where nothing could be read', () => {
    expect(notFoundWord(line('absent'))).toBe('not found')
    expect(notFoundWord(line('untestable'))).toBe('not tested')
  })

  it('says an absent line holds the row at candidate at most, and why the run asked it', () => {
    // At most: a row its evidence put below candidate stays there.
    const asked = notFoundTooltip(line('absent', { expected_snr: 97.28 }))
    expect(asked).toContain('holds the row at candidate at most')
    expect(asked).toContain(
      "The run's detectability gate says the spectrum should show it at this peak's signal-to-noise."
    )
    expect(notFoundTooltip(line('absent', { tof_m2: true }))).toContain(
      'On a TOF a bromine or chlorine reading must show its own M+2 line'
    )
  })

  it('says a line on a peak with no signal-to-noise was asked by its share alone', () => {
    // No expected signal-to-noise: the gate asked the line from the share the
    // run recorded, and the peak has no signal-to-noise to speak of.
    const provenance = { predicted_envelope: { snr: null, asks_share: 0.1, lines: [] } }
    const tip = notFoundTooltip(line('absent'), provenance)
    expect(tip).toContain(
      "The peak has no signal-to-noise, so the run's detectability gate asks for every line from 10% of the row's own line."
    )
    expect(tip).not.toContain("this peak's signal-to-noise")
    expect(tip).toContain('holds the row at candidate at most')
    expect(
      notFoundTooltip(line('absent'), { predicted_envelope: { asks_share: 0.025 } })
    ).toContain('from 2.5% of')
    // A record that does not say the share still says how the line was asked.
    expect(notFoundTooltip(line('absent'))).toContain(
      "asks for lines by their share of the row's own line"
    )
  })

  it('says an untested line takes nothing, and why it could not be read', () => {
    const artifact = notFoundTooltip(line('untestable', { untestable: 'artifact' }))
    expect(artifact).toContain("only an artifact's peak")
    expect(artifact).not.toContain('candidate')
    const range = notFoundTooltip(line('untestable', { untestable: 'out_of_range' }))
    expect(range).toContain('beyond the first or last peak of the spectrum')
    expect(range).toContain('the ends of what it measured')
    expect(range).not.toContain('never')
  })
})

// What the record says of the gate, and of the lines under it, which it does
// not list (MAS-1341).
describe('the gate and the lines not asked', () => {
  const record = (extra = {}) => ({
    predicted_envelope: { snr: 8, detect_snr: 3, asks_share: 0.375, lines: [], ...extra }
  })

  it('reads the gate off the record, and nothing where the run asked nothing', () => {
    expect(envelopeGate(record())).toEqual({ snr: 8, detectSnr: 3, asksShare: 0.375 })
    expect(envelopeGate(record({ snr: null, detect_snr: null, asks_share: 0.1 }))).toEqual({
      snr: null,
      detectSnr: null,
      asksShare: 0.1
    })
    expect(envelopeGate(null)).toBeNull()
    expect(envelopeGate({ predicted_envelope: { lines: [] } })).toBeNull()
    expect(envelopeGate({ predicted_envelope: { asks_share: 0.1 } })).toBeNull()
  })

  it('names the lines not asked by the share of M0 they were asked from', () => {
    const note = notAskedNote(record())
    expect(note).toContain('Not asked: any line predicted under 37.5% of M0')
    expect(note).toContain("At this peak's signal-to-noise of 8")
    expect(note).toContain('would show under 3')
    expect(notAskedNote(record({ snr: null, asks_share: 0.1 }))).toContain(
      'no signal-to-noise for this peak'
    )
    expect(notAskedNote(null)).toBeNull()
    // A TOF's M+2 line is asked under the gate's share, and listed
    expect(notAskedNote(record({ lines: [line('absent', { tof_m2: true })] }))).toContain(
      'under 37.5% of M0, except its own M+2 line, which a TOF always asks.'
    )
    expect(notAskedNote(record({ lines: [line('absent')] }))).not.toContain('M+2')
  })

  it('says why a pattern reads M0 only, where the run asked no line', () => {
    expect(noneAskedSummary(record())).toBe(
      'None asked: every line beyond M0 is predicted under what this peak can show (under 37.5% of M0)'
    )
    expect(noneAskedSummary(record({ lines: [line('absent')] }))).toBeNull()
    expect(noneAskedSummary(null)).toBeNull()
  })

  it('gives the numbers a line was asked at', () => {
    const provenance = record()
    expect(askedNumbers(line('absent', { expected_snr: 7.78 }), provenance)).toBe(
      'Asked: predicted at 97.3% of M0, expected signal-to-noise 7.8, asked from 3.'
    )
    expect(askedNumbers(line('absent'), record({ snr: null, asks_share: 0.1 }))).toBe(
      'Asked: predicted at 97.3% of M0, asked from 10% of M0.'
    )
    expect(askedNumbers(line('absent', { tof_m2: true }), provenance)).toContain(
      'on a TOF a bromine or chlorine reading must show its own M+2 line'
    )
  })

  it("puts the numbers in a not-found line's hover text", () => {
    const tip = notFoundTooltip(line('absent', { expected_snr: 7.78 }), record())
    expect(tip).toContain('expected signal-to-noise 7.8, asked from 3')
    // A TOF's M+2 line says its rule once, and its share
    const m2 = notFoundTooltip(line('absent', { tof_m2: true }), record())
    expect(m2.match(/own M\+2 line/g)).toHaveLength(1)
    expect(m2).toContain('Predicted at 97.3% of M0.')
  })

  it('finds the asked line an isotopologue row sits on, by its peak or its label', () => {
    const found = line('found', { line: '13C', peak_id: 'p-2' })
    const provenance = record({ lines: [found, line('absent')] })
    expect(askedLineOf({ sample_peak_id: 'p-2' }, provenance)).toBe(found)
    expect(askedLineOf({ sample_peak_id: 'p-9', isotope_label: '13C' }, provenance)).toBe(found)
    // An absent line has no row on it
    expect(askedLineOf({ sample_peak_id: 'p-9', isotope_label: '81Br' }, provenance)).toBeNull()
    expect(askedLineOf({ sample_peak_id: 'p-9', isotope_label: '18O' }, provenance)).toBeNull()
  })
})
