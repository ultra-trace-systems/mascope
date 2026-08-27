import { describe, it, expect } from 'vitest'

import { ionizationModeChoices } from '@/lib/ionizationModes'

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
