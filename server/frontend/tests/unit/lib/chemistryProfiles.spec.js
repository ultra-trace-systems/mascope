import { describe, it, expect } from 'vitest'

import {
  ADVANCED_GROUP,
  PROFILES_GROUP,
  chemistryLabel,
  chemistryOptions,
  flatOptions,
  isReadyProfile,
  profiles,
  routedSummary
} from '@/lib/chemistryProfiles'

// The profiles Mascope ships are ionization modes with a system key, listed on
// every server from its first start. One that ships calibrants is ready for a
// file; one with no reagent of its own is not, until a server points it at a
// collection. The server's own modes are the advanced path.

const mode = (id, name, polarity, extra = {}) => ({
  ionization_mode_id: id,
  ionization_mode_name: name,
  ionization_mode_polarity: polarity,
  ionization_mode_token: null,
  system_key: null,
  calibration_collection_id: null,
  ...extra
})

const bromide = mode('sysBromide', 'Bromide, negative', '-', {
  system_key: 'bromide',
  calibration_collection_id: 'sysBromideCal'
})
const nitrate = mode('sysNitrate', 'Nitrate, negative', '-', {
  system_key: 'nitrate',
  calibration_collection_id: 'sysNitrateCal'
})
const deprotonation = mode('sysDeprotonate', 'Deprotonation, negative', '-', {
  system_key: 'deprotonation'
})
const uronium = mode('sysUronium', 'Uronium, positive', '+', {
  system_key: 'uronium',
  calibration_collection_id: 'sysUroniumCal'
})
// Positive, and named between the two negative ones above.
const chargeTransfer = mode('sysAmbientPos', 'Charge transfer (EASY-IC), positive', '+', {
  system_key: 'ambient-positive',
  calibration_collection_id: 'sysAmbientPosCal'
})
const ours = mode('own-br', 'Bromide RI', '-', {
  ionization_mode_token: 'Br',
  calibration_collection_id: 'site-cal'
})
const oursUntokened = mode('own-x', 'Experiment X', '-')

const all = [ours, deprotonation, uronium, nitrate, oursUntokened, bromide]

describe('chemistryOptions', () => {
  it('offers the profiles that calibrate first, and the rest under Advanced', () => {
    expect(chemistryOptions(all, '-')).toEqual([
      {
        label: PROFILES_GROUP,
        items: [
          { label: 'Bromide, negative', value: 'sysBromide' },
          { label: 'Nitrate, negative', value: 'sysNitrate' }
        ]
      },
      {
        label: ADVANCED_GROUP,
        items: [
          { label: 'Bromide RI (Br)', value: 'own-br' },
          { label: 'Deprotonation, negative (no calibrants)', value: 'sysDeprotonate' },
          { label: 'Experiment X', value: 'own-x' }
        ]
      }
    ])
  })

  it('offers only the polarity asked for', () => {
    expect(flatOptions(chemistryOptions(all, '+'))).toEqual([
      { label: 'Uronium, positive', value: 'sysUronium' }
    ])
  })

  it('leaves out a group with nothing in it', () => {
    expect(chemistryOptions([ours], '-').map((group) => group.label)).toEqual([ADVANCED_GROUP])
    expect(chemistryOptions([bromide], '-').map((group) => group.label)).toEqual([PROFILES_GROUP])
    expect(chemistryOptions([], '-')).toEqual([])
  })

  // A server that points a profile with no reagent at a calibrant collection
  // of its own has made it ready: it joins the others.
  it('counts a profile pointed at a collection of the server own as ready', () => {
    const completed = { ...deprotonation, calibration_collection_id: 'site-cal' }
    expect(isReadyProfile(completed)).toBe(true)
    expect(chemistryOptions([completed], '-')[0]).toEqual({
      label: PROFILES_GROUP,
      items: [{ label: 'Deprotonation, negative', value: 'sysDeprotonate' }]
    })
  })
})

describe('chemistryLabel', () => {
  it('names a mode of the server by its token, and a profile by its name', () => {
    expect(chemistryLabel(ours)).toBe('Bromide RI (Br)')
    expect(chemistryLabel(oursUntokened)).toBe('Experiment X')
    expect(chemistryLabel(bromide)).toBe('Bromide, negative')
    expect(chemistryLabel(deprotonation)).toBe('Deprotonation, negative (no calibrants)')
  })
})

describe('profiles', () => {
  it('lists the profiles alone: the ones that calibrate, negative first, by name', () => {
    expect(profiles([...all, chargeTransfer]).map((m) => m.ionization_mode_id)).toEqual([
      'sysBromide',
      'sysNitrate',
      'sysAmbientPos',
      'sysUronium',
      'sysDeprotonate'
    ])
  })
})

describe('routedSummary', () => {
  it('says how many files a mode routed and when the newest was acquired', () => {
    const last = '2026-11-05T14:02:00+00:00'
    expect(routedSummary({ routed_files: 12, last_file_utc: last })).toEqual({
      files: '12 files',
      last: new Date(last).toLocaleString()
    })
    expect(routedSummary({ routed_files: 1, last_file_utc: last }).files).toBe('1 file')
  })

  it('says so when nothing was routed, as an older server leaves the fields out', () => {
    expect(routedSummary({ routed_files: 0, last_file_utc: null })).toEqual({
      files: 'No files yet',
      last: null
    })
    expect(routedSummary({})).toEqual({ files: 'No files yet', last: null })
  })
})
