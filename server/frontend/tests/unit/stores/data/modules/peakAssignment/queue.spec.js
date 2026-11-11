import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// The verification queue's store: it reads the queue of the run the ledger
// shows, and only a run the ledger would read.

const get = vi.fn()
vi.mock('@/api', () => ({
  api: {
    http: { get: (...args) => get(...args), post: vi.fn() },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))
vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: true }))
vi.mock('@/stores/data/modules/sample', () => ({
  useSample: () => ({ focusedId: 'si-1' })
}))
vi.mock('@/stores/auth', () => ({ useAuth: () => ({ user: {}, onLogin: vi.fn() }) }))
let focusedRun
vi.mock('@/stores/data/modules/peakAssignment/run', () => ({
  usePeakAssignmentRun: () => ({ focused: focusedRun })
}))

const ENTRIES = [
  { peak_assignment_id: 'pa-1', reason: 'band_edge', labels_near: 0 },
  { peak_assignment_id: 'pa-2', reason: 'spot_check', labels_near: 2 }
]

let usePeakAssignmentQueue
beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  focusedRun = { peak_assignment_run_id: 'run-1', sample_item_id: 'si-1', status: 'completed' }
  get.mockResolvedValue(ENTRIES)
  ;({ usePeakAssignmentQueue } = await import('@/stores/data/modules/peakAssignment/queue'))
})

describe('peakAssignment queue store', () => {
  it("reads the focused run's queue and counts it", async () => {
    const store = usePeakAssignmentQueue()
    await store.load('test')

    expect(get).toHaveBeenCalledWith(
      '/peak-assignments/sample/si-1/verification-queue',
      expect.objectContaining({ params: { peak_assignment_run_id: 'run-1' }, use: 'read' })
    )
    expect(store.list).toEqual(ENTRIES)
    expect(store.count).toBe(2)
  })

  it('asks nothing for a run still importing, or one of another sample', async () => {
    for (const run of [
      { ...focusedRun, status: 'importing' },
      { ...focusedRun, sample_item_id: 'si-other' },
      null
    ]) {
      focusedRun = run
      setActivePinia(createPinia())
      const store = usePeakAssignmentQueue()
      await store.load('test')
      expect(store.list).toEqual([])
    }
    expect(get).not.toHaveBeenCalled()
  })
})
