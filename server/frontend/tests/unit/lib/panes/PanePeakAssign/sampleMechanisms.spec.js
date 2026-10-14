import { describe, it, expect } from 'vitest'

import { mechanismsOfSample } from '@/lib/panes/PanePeakAssign/sampleMechanisms.js'

// The composition search starts from the mechanisms of the focused sample's
// ionization mode. Reading them used to assume every sample has a mode the
// browser holds, and threw for one that does not - which stopped being rare
// once an acquisition's fragmentation scans could be filed as a sample with no
// ionization mode at all.

const MODES = [
  { ionization_mode_id: 'mode-nitrate', ionization_mechanism_ids: ['mech-2', 'mech-1'] },
  { ionization_mode_id: 'mode-bare', ionization_mechanism_ids: [] }
]
const MECHANISMS = [
  { ionization_mechanism_id: 'mech-1', ionization_mechanism: '[M+NO3]-' },
  { ionization_mechanism_id: 'mech-2', ionization_mechanism: '[M-H]-' }
]

const mechanisms = (sample, modes = MODES, list = MECHANISMS) =>
  mechanismsOfSample(sample, modes, list).map((m) => m.ionization_mechanism_id)

describe('mechanismsOfSample', () => {
  it("gives the mechanisms of the sample's mode, in the mode's order", () => {
    expect(mechanisms({ ionization_mode_id: 'mode-nitrate' })).toEqual(['mech-2', 'mech-1'])
  })

  it('gives none for a sample with no ionization mode', () => {
    // An MS2 sample filed without a chemistry.
    expect(mechanisms({ ionization_mode_id: null })).toEqual([])
    expect(mechanisms({})).toEqual([])
  })

  it('gives none for a mode the browser does not hold', () => {
    // Deleted since the browser loaded the sample that points at it.
    expect(mechanisms({ ionization_mode_id: 'mode-gone' })).toEqual([])
  })

  it('does not take a mode with no id for the mode of a sample with none', () => {
    const modes = [{ ionization_mode_id: undefined, ionization_mechanism_ids: ['mech-1'] }]

    expect(mechanisms({}, modes)).toEqual([])
    expect(mechanisms({ ionization_mode_id: null }, modes)).toEqual([])
  })

  it('gives none for a mode that lists no mechanisms', () => {
    expect(mechanisms({ ionization_mode_id: 'mode-bare' })).toEqual([])
  })

  it('leaves out a mechanism the browser does not hold', () => {
    const modes = [{ ionization_mode_id: 'mode-1', ionization_mechanism_ids: ['mech-9', 'mech-1'] }]

    expect(mechanisms({ ionization_mode_id: 'mode-1' }, modes)).toEqual(['mech-1'])
  })

  it('gives none while no sample is focused', () => {
    expect(mechanisms(null)).toEqual([])
    expect(mechanisms(undefined)).toEqual([])
  })

  it('hands back the mechanisms themselves, not copies', () => {
    // The pane's selector binds these objects, so they have to be the ones in
    // its option list.
    const [first] = mechanismsOfSample({ ionization_mode_id: 'mode-nitrate' }, MODES, MECHANISMS)

    expect(first).toBe(MECHANISMS[1])
  })
})
