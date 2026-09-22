import { describe, it, expect } from 'vitest'

import {
  PROCESSING_STATUSES,
  PROCESSING_STATUS_FILTERS,
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
      expect(tag.tooltip).toBe(PROCESSING_STATUSES[state].description)
    }
  })

  it("prefers the file's own detail to the generic description", () => {
    const tag = processingStatus({
      processing_status: 'needs_chemistry',
      processing_detail: 'No ionization mode tokens found for file x.raw.'
    })

    expect(tag.severity).toBe('warn')
    expect(tag.tooltip).toBe('No ionization mode tokens found for file x.raw.')
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

  it('puts every status of a run still under way under "In progress"', () => {
    // IN_PROGRESS in the backend's config.py: what a restart marks failed.
    const inProgress = PROCESSING_STATUS_FILTERS.find(({ label }) => label === 'In progress')

    expect(inProgress.value.sort()).toEqual(['bound', 'calibrated', 'converted', 'queued'])
  })
})
