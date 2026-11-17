import { describe, it, expect } from 'vitest'

import { runStatusLabel } from '@/lib/runStatus'

// The run selectors name a run's status in words rather than as the API's
// value; the statuses are a sample run's six and a batch run's three.
describe('runStatusLabel', () => {
  it('names a run the server has created and no task has taken up as queued', () => {
    expect(runStatusLabel('pending')).toBe('queued')
  })

  it('keeps the statuses that are words already', () => {
    for (const status of ['running', 'importing', 'completed', 'failed', 'cancelled']) {
      expect(runStatusLabel(status)).toBe(status)
    }
  })

  it('shows a status this build does not know as it came', () => {
    expect(runStatusLabel('paused')).toBe('paused')
    expect(runStatusLabel('constructor')).toBe('constructor')
  })

  it('says nothing for a run with no status', () => {
    expect(runStatusLabel(null)).toBe('')
    expect(runStatusLabel(undefined)).toBe('')
  })
})
