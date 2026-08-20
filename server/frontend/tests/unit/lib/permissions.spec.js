import { describe, it, expect } from 'vitest'

import {
  batchInstruments,
  canCalibrateInstrument,
  canCalibrateInstruments,
  instrumentWorkspace,
  myLevel
} from '@/lib/permissions'

const acq = (instrument, my_role) => ({
  workspace_id: `ws-${instrument}`,
  workspace_name: `Acquisitions ${instrument}`,
  is_system: true,
  my_role
})

const guest = { role_id: 100 }
const editor = { role_id: 200 }
const admin = { role_id: 300 }

describe('instrumentWorkspace', () => {
  it('finds the system workspace for an instrument', () => {
    const workspaces = [acq('orbion', 'admin')]
    expect(instrumentWorkspace(workspaces, 'orbion')?.workspace_id).toBe('ws-orbion')
  })

  it('matches case-insensitively, staying looser than the backend lookup', () => {
    const workspaces = [acq('OrbiHel', 'admin')]
    expect(instrumentWorkspace(workspaces, 'orbihel')?.workspace_id).toBe('ws-OrbiHel')
  })

  it('ignores a non-system workspace with a colliding name', () => {
    const workspaces = [
      { workspace_id: 'decoy', workspace_name: 'Acquisitions orbion', is_system: false }
    ]
    expect(instrumentWorkspace(workspaces, 'orbion')).toBeUndefined()
  })

  it('returns undefined for an unknown instrument or missing input', () => {
    expect(instrumentWorkspace([acq('orbion', 'admin')], 'other')).toBeUndefined()
    expect(instrumentWorkspace(undefined, 'orbion')).toBeUndefined()
    expect(instrumentWorkspace([acq('orbion', 'admin')], undefined)).toBeUndefined()
  })
})

describe('myLevel', () => {
  it('reads my_role as a numeric level', () => {
    expect(myLevel({ my_role: 'admin' })).toBe(300)
    expect(myLevel({ my_role: 'editor' })).toBe(200)
  })

  it('is zero when not a member or the workspace is missing', () => {
    expect(myLevel({ my_role: null })).toBe(0)
    expect(myLevel(undefined)).toBe(0)
  })
})

describe('canCalibrateInstrument', () => {
  it('allows an instrument-workspace admin who is only a global editor', () => {
    // The whole point of moving calibration off the global role.
    expect(canCalibrateInstrument([acq('orbion', 'admin')], editor, 'orbion')).toBe(true)
  })

  it('refuses an instrument-workspace editor', () => {
    expect(canCalibrateInstrument([acq('orbion', 'editor')], editor, 'orbion')).toBe(false)
  })

  it('refuses a non-member of the instrument workspace', () => {
    expect(canCalibrateInstrument([acq('other', 'owner')], editor, 'orbion')).toBe(false)
  })

  it('allows a global admin with no membership at all', () => {
    // Backend bypasses the instrument checks for global admins, including on
    // workspaces created before they were promoted.
    expect(canCalibrateInstrument([], admin, 'orbion')).toBe(true)
  })

  it('refuses a global guest who is not an instrument-workspace admin', () => {
    expect(canCalibrateInstrument([acq('orbion', 'guest')], guest, 'orbion')).toBe(false)
  })

  it('allows a global guest who IS an instrument-workspace admin', () => {
    // The layers are independent; the global role does not cap workspace roles.
    expect(canCalibrateInstrument([acq('orbion', 'admin')], guest, 'orbion')).toBe(true)
  })

  it('stays enabled when the instrument is unknown', () => {
    expect(canCalibrateInstrument([], editor, undefined)).toBe(true)
  })
})

describe('canCalibrateInstruments', () => {
  const workspaces = [acq('orbion', 'admin'), acq('tofwerk', 'editor')]

  it('requires every instrument to pass', () => {
    expect(canCalibrateInstruments(workspaces, editor, ['orbion'])).toBe(true)
    expect(canCalibrateInstruments(workspaces, editor, ['orbion', 'tofwerk'])).toBe(false)
  })

  it('stays enabled when no instruments are known', () => {
    expect(canCalibrateInstruments(workspaces, editor, [])).toBe(true)
    expect(canCalibrateInstruments(workspaces, editor, undefined)).toBe(true)
  })
})

describe('batchInstruments', () => {
  const samples = [
    { sample_batch_id: 'b1', instrument: 'orbion' },
    { sample_batch_id: 'b1', instrument: 'orbion' },
    { sample_batch_id: 'b1', instrument: 'tofwerk' },
    { sample_batch_id: 'b2', instrument: 'other' }
  ]

  it('returns the distinct instruments for one batch', () => {
    expect(batchInstruments(samples, 'b1').sort()).toEqual(['orbion', 'tofwerk'])
  })

  it('returns an empty list when nothing is loaded for the batch', () => {
    expect(batchInstruments(samples, 'b3')).toEqual([])
    expect(batchInstruments(undefined, 'b1')).toEqual([])
  })
})
