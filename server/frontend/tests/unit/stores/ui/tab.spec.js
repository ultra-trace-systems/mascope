import { describe, it, expect, vi, beforeEach } from 'vitest'
import { reactive, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// The tab store only watches a few slices of the data store; a reactive stub
// keeps the real (heavy) data store out of this unit test.
const { state, flags } = vi.hoisted(() => ({
  state: { data: null },
  flags: { peakAssignment: false }
}))
vi.mock('@/stores/data', () => ({ useData: () => state.data }))
// A getter so each test can flip the peak-centric assignment flag.
vi.mock('@/lib/features', () => ({
  get peakAssignmentEnabled() {
    return flags.peakAssignment
  }
}))

import { useTab } from '@/stores/ui/tab'

const makeData = () =>
  reactive({
    match: { visualized: { ion: null } },
    sample: { focused: null, list: [] },
    batch: { focused: null }
  })

describe('tab store: match-tab auto-switch', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    state.data = makeData()
    flags.peakAssignment = false
  })

  it('switches to the match tab when an ion is visualized (flag off)', async () => {
    const tab = useTab()
    tab.active = 'batch'

    state.data.match.visualized.ion = { target_ion_id: 'i1' }
    await nextTick()

    expect(tab.active).toBe('match')
  })

  it('returns to batch when the visualization clears (flag off)', async () => {
    const tab = useTab()
    state.data.batch.focused = { sample_batch_id: 'b1' }
    state.data.match.visualized.ion = { target_ion_id: 'i1' }
    await nextTick()
    expect(tab.active).toBe('match')

    state.data.match.visualized.ion = null
    await nextTick()

    expect(tab.active).toBe('batch')
  })

  it('never auto-switches to the retired match tab with peak-centric assignment on', async () => {
    // With the flag on the Dashboard does not render the Match tab (#1736), so
    // the auto-switch into it stands down as well.
    flags.peakAssignment = true
    const tab = useTab()
    tab.active = 'batch'

    state.data.match.visualized.ion = { target_ion_id: 'i1' }
    await nextTick()

    expect(tab.active).toBe('batch')
  })
})
