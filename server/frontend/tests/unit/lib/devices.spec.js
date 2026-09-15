import { describe, it, expect } from 'vitest'

import { deviceMetaLabel } from '@/lib/devices'

const device = (overrides = {}) => ({
  service_name: 'file-agent',
  instrument: null,
  last_seen_version: null,
  ...overrides
})

describe('deviceMetaLabel', () => {
  it('leaves out what a machine has not reported', () => {
    // A machine paired by an agent that predates these fields reports
    // neither, and must still read as a complete line rather than one with
    // gaps in it.
    expect(deviceMetaLabel(device(), 'File Agent', 'never seen')).toBe(
      'File Agent · never seen'
    )
  })

  it('labels the instrument and the release', () => {
    expect(
      deviceMetaLabel(
        device({ instrument: 'Orbi-Lab2', last_seen_version: '2.0.0' }),
        'File Agent',
        'last seen 3.9.2026'
      )
    ).toBe('File Agent · watching Orbi-Lab2 · agent 2.0.0 · last seen 3.9.2026')
  })

  it('tells a lone instrument from a lone release', () => {
    // Unlabelled these two render the same shape, so a half-upgraded fleet -
    // which is exactly what the column is for reading - would be ambiguous.
    const watching = deviceMetaLabel(
      device({ instrument: 'Orbi-Lab2' }),
      'File Agent',
      'never seen'
    )
    const running = deviceMetaLabel(
      device({ last_seen_version: 'Orbi-Lab2' }),
      'File Agent',
      'never seen'
    )
    expect(watching).not.toBe(running)
    expect(watching).toContain('watching Orbi-Lab2')
    expect(running).toContain('agent Orbi-Lab2')
  })

  it('anchors each reported value behind its own label', () => {
    // Both values are free text from the machine, and the server strips the
    // separator before storing them. The label is the second half of that:
    // whatever arrives is read as one field, not as several.
    const label = deviceMetaLabel(
      device({ last_seen_version: '1.0 last seen just now' }),
      'File Agent',
      'never seen'
    )
    expect(label).toBe('File Agent · agent 1.0 last seen just now · never seen')
    expect(label.split(' · ')).toHaveLength(3)
  })
})
