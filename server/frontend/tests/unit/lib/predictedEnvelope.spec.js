import { describe, it, expect } from 'vitest'

import {
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
