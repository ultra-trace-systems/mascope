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

  it('auto-switches to the match tab with peak-centric assignment on too', async () => {
    // The Dashboard renders the Match tab either way - the two paradigms
    // coexist - so visualizing an ion always has somewhere to go. Standing this
    // watcher down under the flag once left the visualization set with no tab
    // showing it.
    flags.peakAssignment = true
    const tab = useTab()
    tab.active = 'batch'

    state.data.match.visualized.ion = { target_ion_id: 'i1' }
    await nextTick()

    expect(tab.active).toBe('match')
  })

  it('leaves a user working in the sample tab where they are', async () => {
    // The one case the auto-switch defers to: an assignment-first user with the
    // Sample tab open is not yanked into Match by a stray visualization.
    flags.peakAssignment = true
    const tab = useTab()
    tab.active = 'sample'

    state.data.match.visualized.ion = { target_ion_id: 'i1' }
    await nextTick()

    expect(tab.active).toBe('sample')
  })
})
