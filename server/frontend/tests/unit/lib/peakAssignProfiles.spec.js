import { describe, it, expect } from 'vitest'

import {
  chemistryLabel,
  contextName,
  polarityMismatches,
  polarityWord,
  profileName,
  runChemistry
} from '@/lib/peakAssignProfiles'

// How the launcher and a run's chip name the chemistry a run searches under.
// The identity profile and context are the layer switched off, and a label of
// "None" beside "None" says nothing, so they are named as the absence they are.

const BROMIDE = {
  profile: 'BR',
  profile_label: 'Bromide CIMS',
  profile_polarity: '-',
  context: 'ambient-air',
  context_label: 'Ambient air',
  polarity: '-'
}

describe('profile and context names', () => {
  it('uses the served labels', () => {
    expect(profileName(BROMIDE)).toBe('Bromide CIMS')
    expect(contextName(BROMIDE)).toBe('Ambient air')
    expect(chemistryLabel(BROMIDE)).toBe('Bromide CIMS · Ambient air')
  })

  it('names the identity entries as what they switch off', () => {
    const identity = {
      profile: 'none',
      profile_label: 'None',
      context: 'none',
      context_label: 'None'
    }
    expect(chemistryLabel(identity)).toBe('No profile · No context')
  })

  it('falls back to the key where a record carries no label', () => {
    expect(chemistryLabel({ profile: 'BR', context: 'chamber' })).toBe('BR · chamber')
  })

  it('names nothing for no record', () => {
    expect(profileName(null)).toBe('')
    expect(contextName({})).toBe('')
    expect(chemistryLabel(undefined)).toBe('')
  })
})

describe('polarityMismatches', () => {
  it('finds a profile applied to samples of the other polarity', () => {
    const positive = { ...BROMIDE, polarity: '+' }
    expect(polarityMismatches([BROMIDE, positive])).toEqual([positive])
  })

  it('never counts the identity profile, which has no polarity', () => {
    expect(polarityMismatches([{ ...BROMIDE, profile_polarity: '', polarity: '+' }])).toEqual([])
  })

  it('ignores a sample whose polarity is not recorded', () => {
    expect(polarityMismatches([{ ...BROMIDE, polarity: null }])).toEqual([])
    expect(polarityMismatches(null)).toEqual([])
  })

  it('reads only the two signs as polarities', () => {
    expect(polarityWord('+')).toBe('positive')
    expect(polarityWord('-')).toBe('negative')
    expect(polarityWord('toString')).toBeNull()
    expect(polarityWord('pos')).toBeNull()
  })
})

describe('runChemistry', () => {
  it('reads the chemistry a run recorded', () => {
    const run = { config: { profile: 'auto', resolved_profile: BROMIDE } }
    expect(runChemistry(run)).toBe(BROMIDE)
  })

  it('has none for a run that recorded none, or a record that is not one', () => {
    for (const run of [
      null,
      {},
      { config: null },
      { config: { profile: 'auto' } },
      { config: { resolved_profile: 'BR' } },
      { config: { resolved_profile: [BROMIDE] } },
      { config: { resolved_profile: { profile: '' } } },
      { config: { resolved_profile: { profile: 7 } } }
    ]) {
      expect(runChemistry(run)).toBeNull()
    }
  })
})
