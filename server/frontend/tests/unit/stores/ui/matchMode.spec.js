import { describe, it, expect, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// A getter so each test can flip the peak-centric assignment flag, the way
// tab.spec.js does. The store reads the flag inside its setup body, so a fresh
// pinia is enough to pick up a change - no module reset needed.
const { flags } = vi.hoisted(() => ({ flags: { peakAssignment: true } }))
vi.mock('@/lib/features', () => ({
  get peakAssignmentEnabled() {
    return flags.peakAssignment
  }
}))

import { useMatchMode, MODE_OPTIONS } from '@/stores/ui/matchMode'

const MODE_KEY = 'mascope.browserMatch.mode'

describe('matchMode store', () => {
  beforeEach(() => {
    // Pinia caches store instances and the localStorage shim is not cleared
    // between tests, so both have to be reset for the seed path to run again.
    localStorage.clear()
    setActivePinia(createPinia())
    flags.peakAssignment = true
  })

  it('offers exactly the two paradigms, Targets first', () => {
    expect(MODE_OPTIONS).toEqual([
      { label: 'Targets', value: 'targets' },
      { label: 'Assignments', value: 'assignments' }
    ])
    expect(useMatchMode().options).toEqual(MODE_OPTIONS)
  })

  it('defaults to targets with nothing stored', () => {
    expect(useMatchMode().mode).toBe('targets')
  })

  it('restores the stored choice, so it survives a reload', () => {
    localStorage.setItem(MODE_KEY, 'assignments')

    expect(useMatchMode().mode).toBe('assignments')
  })

  it('persists a change under the key the browser toggle already used', async () => {
    const store = useMatchMode()

    store.mode = 'assignments'
    await nextTick()

    expect(localStorage.getItem(MODE_KEY)).toBe('assignments')
  })

  it('falls back to targets on an unrecognised stored value', () => {
    // The two consumers branch on opposite comparisons (=== 'assignments' in
    // the browser, === 'targets' in the batch tab), so carrying an unknown
    // mode would put them on opposite sides - the split this store prevents.
    localStorage.setItem(MODE_KEY, 'nonsense')

    expect(useMatchMode().mode).toBe('targets')
  })

  describe('with peak-centric assignment off', () => {
    beforeEach(() => {
      flags.peakAssignment = false
    })

    it('pins targets regardless of any stored preference', () => {
      localStorage.setItem(MODE_KEY, 'assignments')

      expect(useMatchMode().mode).toBe('targets')
    })

    it('does not write back, so a flag-on choice is not erased', async () => {
      // Seed 'targets' and move to 'assignments'. The flag-off store pins
      // 'targets', so this is a real change and the persistence watch actually
      // runs; seeding 'assignments' instead would make the assignment a no-op,
      // the ref would never trigger, and the assertion would hold even with no
      // guard at all.
      localStorage.setItem(MODE_KEY, 'targets')
      const store = useMatchMode()

      store.mode = 'assignments'
      await nextTick()

      expect(localStorage.getItem(MODE_KEY)).toBe('targets')
    })
  })
})
