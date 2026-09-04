import { describe, it, expect } from 'vitest'

import { assignmentStatus } from '@/lib/assignmentStatus'

const record = (over = {}) => ({
  sample_item_id: 's-1',
  run: null,
  n_members: 3,
  n_assigned: 2,
  ...over
})

describe('assignmentStatus', () => {
  it('reads as unknown while nothing has loaded', () => {
    expect(assignmentStatus(null).state).toBe('unknown')
    expect(assignmentStatus(undefined).tooltip).toContain('not loaded')
  })

  it('names a run of its own, its engine and what the ledger holds', () => {
    const badge = assignmentStatus(
      record({
        run: {
          peak_assignment_run_id: 'r-1',
          engine: 'peaky',
          engine_version: '0.7.0',
          peak_assignment_run_utc_created: '2026-09-04T10:00:00Z'
        }
      })
    )
    expect(badge.state).toBe('run')
    expect(badge.tooltip).toContain('Assigned by peaky 0.7.0')
    expect(badge.tooltip).toContain('a run of its own')
    expect(badge.tooltip).toContain('2 of 3 peaks assigned in the batch ledger')
  })

  it('says a sample is served from the ledger when it has no run of its own', () => {
    const badge = assignmentStatus(record())
    expect(badge.state).toBe('ledger')
    expect(badge.tooltip).toContain('Served from the batch ledger')
    expect(badge.tooltip).toContain('2 of 3 peaks')
  })

  it('says nothing is assigned, in the ledger or out of it', () => {
    const inLedger = assignmentStatus(record({ n_assigned: 0 }))
    expect(inLedger.state).toBe('none')
    expect(inLedger.tooltip).toContain('No assignments yet: 0 of 3 peaks')

    const outside = assignmentStatus(record({ n_members: 0, n_assigned: 0 }))
    expect(outside.state).toBe('none')
    expect(outside.tooltip).toContain('not in the batch ledger')
  })

  it('keeps one icon for every state, so the column reads as one thing', () => {
    const icons = new Set(
      [
        null,
        record(),
        record({ n_assigned: 0 }),
        record({ run: { engine: 'mascope', engine_version: '1' } })
      ].map((r) => assignmentStatus(r).icon)
    )
    expect(icons.size).toBe(1)
  })
})
