import { describe, it, expect, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

import { useAssignmentStages } from '@/stores/ui/assignmentStages'

// Show assignment stages: a user setting, off by default, kept in this
// browser like the theme.
describe('useAssignmentStages', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('is off by default', () => {
    expect(useAssignmentStages().show).toBe(false)
  })

  it('persists a change and reads it back', async () => {
    useAssignmentStages().show = true
    await nextTick()
    expect(localStorage.getItem('mascope.assignment.showStages')).toBe('true')

    setActivePinia(createPinia())
    expect(useAssignmentStages().show).toBe(true)
  })
})
