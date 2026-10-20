import { describe, it, expect, afterEach, vi } from 'vitest'

import { browserTimezone } from '@/lib/browserTimezone'

// The zone a browser upload reports for its acquisition times. Only a
// geographic zone is a reading of where the browser is: a placeholder such
// as UTC, which fingerprinting-resistant, headless and kiosk browsers report
// wherever they stand, would be stored as a fact and leave the file exactly
// where the old guess did, no longer flagged.

const reporting = (timeZone) =>
  vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({ timeZone })

describe('browserTimezone', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it.each([
    'Europe/Vienna',
    'Europe/London',
    'Atlantic/Reykjavik',
    'America/Port-au-Prince',
    'America/Argentina/Buenos_Aires',
    'Australia/Lord_Howe',
    'Pacific/Chatham'
  ])('names a geographic zone as it is: %s', (zone) => {
    reporting(zone)

    expect(browserTimezone()).toBe(zone)
  })

  it.each(['UTC', 'Etc/UTC', 'Etc/GMT+2', 'GMT', 'Factory', '', undefined, null])(
    'names nothing for a placeholder: %s',
    (zone) => {
      reporting(zone)

      expect(browserTimezone()).toBeUndefined()
    }
  )

  it('names nothing when the browser cannot say', () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockImplementation(() => {
      throw new RangeError('no zone')
    })

    expect(browserTimezone()).toBeUndefined()
  })
})
