import { describe, it, expect } from 'vitest'

import {
  PROCESSING_STATUSES,
  PROCESSING_STATUS_FILTERS,
  STALLED_AFTER_MS,
  canChooseChemistry,
  isStalled,
  processingStatus
} from '@/lib/processingStatus'

// The values the backend writes (ProcessingStatus in
// server/backend/src/mascope_backend/api/models/sample/files/config.py).
const BACKEND_STATUSES = [
  'converted',
  'queued',
  'bound',
  'calibrated',
  'needs_chemistry',
  'calibration_failed',
  'done',
  'failed'
]

describe('processingStatus', () => {
  it('shows nothing for a file processed before the status was recorded', () => {
    for (const file of [null, undefined, {}, { processing_status: null }]) {
      expect(processingStatus(file)).toBe(null)
    }
  })

  it('has a tag for every status the backend writes', () => {
    expect(Object.keys(PROCESSING_STATUSES).sort()).toEqual([...BACKEND_STATUSES].sort())
    for (const state of BACKEND_STATUSES) {
      const tag = processingStatus({ processing_status: state })
      expect(tag.state).toBe(state)
      expect(tag.label).toBeTruthy()
      // What the status means, and for one that asks for a person, what to do.
      expect(tag.tooltip.split('\n')[0]).toBe(PROCESSING_STATUSES[state].description)
    }
  })

  it("prefers the file's own detail to the generic description", () => {
    const tag = processingStatus({
      processing_status: 'needs_chemistry',
      processing_detail: 'No ionization mode tokens found for file x.raw.'
    })

    expect(tag.severity).toBe('warn')
    expect(tag.tooltip.split('\n')[0]).toBe('No ionization mode tokens found for file x.raw.')
  })

  it('says when the status was recorded', () => {
    const tag = processingStatus({
      processing_status: 'done',
      processing_updated_utc: '2026-09-21T10:00:00+00:00'
    })

    expect(tag.tooltip).toContain('Recorded ')
  })

  it('tells failures from outcomes that only need attention', () => {
    expect(processingStatus({ processing_status: 'failed' }).severity).toBe('danger')
    expect(processingStatus({ processing_status: 'calibration_failed' }).severity).toBe('warn')
    expect(processingStatus({ processing_status: 'done' }).severity).toBe('success')
  })

  it('shows a status this build does not know under its own name', () => {
    const tag = processingStatus({ processing_status: 'split' })

    expect(tag.label).toBe('split')
    expect(tag.severity).toBe('secondary')
  })
})

describe('PROCESSING_STATUS_FILTERS', () => {
  it('only offers statuses the backend writes', () => {
    for (const { value } of PROCESSING_STATUS_FILTERS) {
      for (const state of value ?? []) {
        expect(BACKEND_STATUSES).toContain(state)
      }
    }
  })

  it('puts every status that asks for a person under "Needs attention"', () => {
    const attention = PROCESSING_STATUS_FILTERS.find(({ label }) => label === 'Needs attention')

    expect(attention.value.sort()).toEqual(['calibration_failed', 'failed', 'needs_chemistry'])
  })

  it('offers each status that asks for a person on its own', () => {
    // What a kept notification's Show files sets, so the dropdown can show it.
    for (const state of ['needs_chemistry', 'calibration_failed', 'failed']) {
      const choice = PROCESSING_STATUS_FILTERS.find(
        ({ value }) => value?.length === 1 && value[0] === state
      )
      expect(choice?.label).toBe(PROCESSING_STATUSES[state].label)
    }
  })

  it('puts every status of a run still under way under "In progress"', () => {
    // IN_PROGRESS in the backend's config.py: what a restart marks failed.
    const inProgress = PROCESSING_STATUS_FILTERS.find(({ label }) => label === 'In progress')

    expect(inProgress.value.sort()).toEqual(['bound', 'calibrated', 'converted', 'queued'])
  })
})

describe('what a file that needs a chemistry says', () => {
  it("gives the file's own reason, then what to do about it", () => {
    const tag = processingStatus({
      processing_status: 'needs_chemistry',
      processing_detail: 'No ionization mode tokens found for file x.raw.'
    })

    expect(tag.tooltip.split('\n')).toEqual([
      'No ionization mode tokens found for file x.raw.',
      PROCESSING_STATUSES.needs_chemistry.action
    ])
  })
})

describe('canChooseChemistry', () => {
  const row = (processing_status) => ({ sample_file_id: processing_status, processing_status })

  it('offers files no run is working on', () => {
    for (const state of ['needs_chemistry', 'failed', 'done', 'calibration_failed', null]) {
      expect(canChooseChemistry([row(state)])).toBe(true)
    }
  })

  it('leaves a selection alone while any of it is being processed', () => {
    for (const state of ['converted', 'queued', 'bound', 'calibrated']) {
      expect(canChooseChemistry([row('needs_chemistry'), row(state)])).toBe(false)
    }
    expect(canChooseChemistry([])).toBe(false)
  })

  it('offers a file whose run stalled', () => {
    const stalled = {
      ...row('bound'),
      processing_updated_utc: new Date(Date.now() - STALLED_AFTER_MS - 60_000).toISOString()
    }

    expect(canChooseChemistry([row('needs_chemistry'), stalled])).toBe(true)
  })
})

describe('isStalled', () => {
  const now = Date.parse('2026-09-22T12:00:00Z')
  const file = (processing_status, hoursAgo) => ({
    processing_status,
    processing_updated_utc: new Date(now - hoursAgo * 3_600_000).toISOString()
  })

  it('tells a run that recorded nothing for over a day', () => {
    expect(isStalled(file('queued', 25), now)).toBe(true)
    expect(isStalled(file('queued', 23), now)).toBe(false)
  })

  it('only judges a run still under way', () => {
    expect(isStalled(file('failed', 48), now)).toBe(false)
    expect(isStalled({ processing_status: 'converted' }, now)).toBe(false)
  })

  it('says so under the status', () => {
    const recorded = (hoursAgo) => ({
      processing_status: 'converted',
      processing_updated_utc: new Date(Date.now() - hoursAgo * 3_600_000).toISOString()
    })

    expect(processingStatus(recorded(48)).tooltip).toContain(
      'the run has stopped. Re-process the file.'
    )
    expect(processingStatus(recorded(1)).tooltip).not.toContain('stopped')
  })
})
