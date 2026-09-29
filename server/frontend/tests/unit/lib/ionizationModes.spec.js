import { describe, it, expect } from 'vitest'

import {
  choicesForScope,
  hasIonizationToken,
  instrumentChoices,
  ionizationModeChoices
} from '@/lib/ionizationModes'

const mode = (id, name, token, polarity) => ({
  ionization_mode_id: id,
  ionization_mode_name: name,
  ionization_mode_token: token,
  ionization_mode_polarity: polarity
})

const MODES = [
  mode('m2', 'Nitrate', 'NO3', '-'),
  mode('m1', 'Ammonium', 'NH4', '+'),
  mode('m3', 'Proton transfer', 'PTR', '+'),
  mode('m4', 'Untokenized', null, '+')
]

describe('ionizationModeChoices', () => {
  it('offers every mode of the polarity, sorted by name', () => {
    const { options } = ionizationModeChoices({
      modes: MODES,
      filename: 'inst_NH4_001.raw',
      polarity: '+'
    })
    expect(options).toEqual([
      { label: 'Ammonium', value: 'm1' },
      { label: 'Proton transfer', value: 'm3' },
      { label: 'Untokenized', value: 'm4' }
    ])
  })

  it('preselects the mode whose token the filename carries', () => {
    const { defaultId } = ionizationModeChoices({
      modes: MODES,
      filename: 'inst_NH4_001.raw',
      polarity: '+'
    })
    expect(defaultId).toBe('m1')
  })

  it('still offers the polarity when the filename matches no token', () => {
    const { options, defaultId } = ionizationModeChoices({
      modes: MODES,
      filename: 'inst_20240101_001.raw',
      polarity: '+'
    })
    expect(options.map((o) => o.value)).toEqual(['m1', 'm3', 'm4'])
    expect(defaultId).toBeNull()
  })

  it('leaves the choice open when overlapping tokens both match', () => {
    const { defaultId } = ionizationModeChoices({
      modes: MODES,
      filename: 'inst_NH4_PTR_001.raw',
      polarity: '+'
    })
    expect(defaultId).toBeNull()
  })

  it('ignores a token that matches in the other polarity', () => {
    const { options, defaultId } = ionizationModeChoices({
      modes: MODES,
      filename: 'inst_NH4_001.raw',
      polarity: '-'
    })
    expect(options).toEqual([{ label: 'Nitrate', value: 'm2' }])
    expect(defaultId).toBeNull()
  })

  it('offers nothing until a mixed-polarity file has a polarity picked', () => {
    expect(
      ionizationModeChoices({ modes: MODES, filename: 'inst_NH4_NO3_001.raw', polarity: '+-' })
    ).toEqual({ options: [], defaultId: null, reason: 'no-token' })
    expect(
      ionizationModeChoices({ modes: MODES, filename: 'inst_NH4_NO3_001.raw', polarity: null })
    ).toEqual({ options: [], defaultId: null, reason: 'no-token' })
  })

  it('survives an unloaded mode list and a missing filename', () => {
    expect(ionizationModeChoices()).toEqual({ options: [], defaultId: null, reason: 'no-token' })
    expect(ionizationModeChoices({ modes: MODES, filename: null, polarity: '+' })).toEqual({
      options: [
        { label: 'Ammonium', value: 'm1' },
        { label: 'Proton transfer', value: 'm3' },
        { label: 'Untokenized', value: 'm4' }
      ],
      defaultId: null,
      reason: 'no-token'
    })
  })

  it('says why no mode was preselected, so the two empty cases read apart', () => {
    const reasonFor = (filename) =>
      ionizationModeChoices({ modes: MODES, filename, polarity: '+' }).reason
    expect(reasonFor('inst_NH4_001.raw')).toBe('resolved')
    expect(reasonFor('inst_20240101_001.raw')).toBe('no-token')
    // Both tokens are really in the filename, so telling the user it carries
    // none would be false - the ambiguity is the reason, not their naming.
    expect(reasonFor('inst_NH4_PTR_001.raw')).toBe('ambiguous')
  })
})

describe('hasIonizationToken', () => {
  it("finds a configured mode's token in the name", () => {
    expect(hasIonizationToken('inst_2026_NO3_001.raw', MODES)).toBe(true)
  })

  it('finds none in a name without one', () => {
    expect(hasIonizationToken('inst_2026_001.raw', MODES)).toBe(false)
    expect(hasIonizationToken('inst_2026_001.raw', [])).toBe(false)
  })

  it('never counts a mode that has no token', () => {
    // `'...'.includes(null)` searches for the text "null".
    expect(hasIonizationToken('inst_null_001.raw', [mode('m4', 'Untokenized', null, '+')])).toBe(
      false
    )
  })
})

// --- a mode may belong to one instrument (#1463) ---------------------------

const scoped = (id, name, token, polarity, instrument) => ({
  ...mode(id, name, token, polarity),
  instrument
})

describe('the instrument a mode belongs to', () => {
  const NITRATE_ANY = mode('any', 'Nitrate shared', 'NO3', '-')
  const NITRATE_ON_A = scoped('a', 'Nitrate 15N', 'NO3', '-', 'ORBI-A')
  const NITRATE_ON_B = scoped('b', 'Nitrate natural', 'NO3', '-', 'ORBI-B')

  it('offers a mode of another instrument to nobody', () => {
    const { options } = ionizationModeChoices({
      modes: [NITRATE_ON_A, NITRATE_ON_B],
      filename: 'ORBI-A_NO3_001.raw',
      polarity: '-',
      instrument: 'ORBI-A'
    })
    expect(options).toEqual([{ label: 'Nitrate 15N', value: 'a' }])
  })

  it('preselects each instrument its own chemistry for the same token', () => {
    // The headline case. Without the scope both files read as ambiguous, and a
    // file of one instrument could be preselected with the other's chemistry -
    // which silently mismatches the whole sample.
    const modes = [NITRATE_ON_A, NITRATE_ON_B]
    expect(
      ionizationModeChoices({
        modes,
        filename: 'ORBI-A_NO3_001.raw',
        polarity: '-',
        instrument: 'ORBI-A'
      })
    ).toMatchObject({ defaultId: 'a', reason: 'resolved' })
    expect(
      ionizationModeChoices({
        modes,
        filename: 'ORBI-B_NO3_001.raw',
        polarity: '-',
        instrument: 'ORBI-B'
      })
    ).toMatchObject({ defaultId: 'b', reason: 'resolved' })
  })

  it('prefers the instrument own mode over the shared one for a token', () => {
    expect(
      ionizationModeChoices({
        modes: [NITRATE_ANY, NITRATE_ON_A],
        filename: 'ORBI-A_NO3_001.raw',
        polarity: '-',
        instrument: 'ORBI-A'
      })
    ).toMatchObject({ defaultId: 'a', reason: 'resolved' })
  })

  it('leaves the shared mode to preselect where the instrument has none', () => {
    expect(
      ionizationModeChoices({
        modes: [NITRATE_ANY, NITRATE_ON_A],
        filename: 'ORBI-B_NO3_001.raw',
        polarity: '-',
        instrument: 'ORBI-B'
      })
    ).toMatchObject({ defaultId: 'any', reason: 'resolved' })
  })

  it('still calls two unrelated tokens ambiguous', () => {
    // The scope resolves one token meaning two things, not a name that names
    // two chemistries. That stays the user's to answer.
    const bromide = mode('br', 'Bromide shared', 'BR', '-')
    expect(
      ionizationModeChoices({
        modes: [bromide, NITRATE_ON_A],
        filename: 'ORBI-A_BR_NO3_001.raw',
        polarity: '-',
        instrument: 'ORBI-A'
      })
    ).toMatchObject({ defaultId: null, reason: 'ambiguous' })
  })

  it('compares the instrument case-insensitively', () => {
    // SampleFile.instrument is recorded with inconsistent case.
    expect(
      ionizationModeChoices({
        modes: [NITRATE_ON_A],
        filename: 'orbi-a_NO3_001.raw',
        polarity: '-',
        instrument: 'orbi-a'
      })
    ).toMatchObject({ defaultId: 'a', reason: 'resolved' })
  })

  it('applies every mode when the instrument is not known', () => {
    // The upload notice runs before a file is converted. Hiding scoped modes
    // there would report a file as carrying no configured token.
    expect(hasIonizationToken('ORBI-A_NO3_001.raw', [NITRATE_ON_A])).toBe(true)
  })

  it('leaves out another instrument mode once the instrument is known', () => {
    expect(hasIonizationToken('ORBI-B_NO3_001.raw', [NITRATE_ON_A], 'ORBI-B')).toBe(false)
  })
})

describe('the override the instrument gets', () => {
  const NITRATE_ANY = mode('any', 'Nitrate shared', 'NO3', '-')
  const NITRATE_ON_A = scoped('a', 'Nitrate 15N', 'NO3', '-', 'ORBI-A')

  it('does not let a shorter scoped token beat a longer shared one', () => {
    // A shared NO3_15N beside an instrument's NO3: the shared mode reads the
    // name precisely, so the scope does not win on the shorter token and the
    // file is left to the user.
    const sharedLonger = mode('long', 'Nitrate 15N shared', 'NO3_15N', '-')
    expect(
      ionizationModeChoices({
        modes: [sharedLonger, NITRATE_ON_A],
        filename: 'ORBI-A_NO3_15N_001.raw',
        polarity: '-',
        instrument: 'ORBI-A'
      })
    ).toMatchObject({ defaultId: null, reason: 'ambiguous' })
  })

  it('does not run at all when the instrument is unknown', () => {
    // Every mode applies then, so dropping the shared match would hand the file
    // whichever instrument's scoped mode happened to match - the wrong
    // chemistry, for a file whose instrument nobody has established.
    expect(
      ionizationModeChoices({
        modes: [NITRATE_ANY, NITRATE_ON_A],
        filename: 'unknown_NO3_001.raw',
        polarity: '-'
      })
    ).toMatchObject({ defaultId: null, reason: 'ambiguous' })
  })
})

describe('the instrument choices a mode is offered', () => {
  it('lists one option per instrument, however its name was recorded', () => {
    // /instruments groups by exact spelling, while the scope folds the case.
    expect(instrumentChoices([{ instrument: 'ORBI-1' }, { instrument: 'orbi-1' }])).toEqual([
      { label: 'ORBI-1', value: 'ORBI-1' }
    ])
  })

  it('leaves out a blank instrument name', () => {
    expect(instrumentChoices([{ instrument: '' }, { instrument: null }])).toEqual([])
  })

  it('offers the stored spelling rather than a second option for it', () => {
    // A mode stored as orbi-1 beside a listed ORBI-1 used to get both.
    const options = instrumentChoices([{ instrument: 'ORBI-1' }])
    expect(choicesForScope(options, 'orbi-1')).toEqual([{ label: 'ORBI-1', value: 'orbi-1' }])
  })

  it('adds a scope the list does not carry at all', () => {
    // Set through the API, or its files are gone; the scope is still in force.
    const options = instrumentChoices([{ instrument: 'ORBI-1' }])
    expect(choicesForScope(options, 'ORBI-9')).toEqual([
      { label: 'ORBI-1', value: 'ORBI-1' },
      { label: 'ORBI-9', value: 'ORBI-9' }
    ])
  })

  it('leaves the list alone for a mode with no scope', () => {
    const options = instrumentChoices([{ instrument: 'ORBI-1' }])
    expect(choicesForScope(options, null)).toBe(options)
  })
})
