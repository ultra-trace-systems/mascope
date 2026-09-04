import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const get = vi.fn()
vi.mock('@/api', () => ({
  api: {
    http: { get: (...args) => get(...args), post: vi.fn() },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))
vi.mock('@/stores/data/modules/batch', () => ({
  useBatch: () => ({ focusedId: 'sb-1' })
}))
// The run the ledger is reading, as the run store reports it: null is the
// live ledger, an id is an earlier run's snapshot.
const runState = { viewingId: null }
vi.mock('@/stores/data/modules/batchPeak/run', () => ({
  useBatchPeakRun: () => runState
}))
vi.mock('@/stores/auth', () => ({ useAuth: () => ({ user: {}, onLogin: vi.fn() }) }))

let useBatchPeakLedger
beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  runState.viewingId = null
  get.mockResolvedValue([])
  ;({ useBatchPeakLedger } = await import('@/stores/data/modules/batchPeak/ledger'))
})

describe('batchPeak ledger store and the run it reads', () => {
  it('reads the live ledger with no run named', async () => {
    const store = useBatchPeakLedger()
    await store.load('test')
    expect(get).toHaveBeenCalledWith(
      '/batch-peaks/batch/sb-1',
      expect.objectContaining({ params: { min_n_present: 1 } })
    )
  })

  it("names the run on the read when an earlier run's snapshot is on screen", async () => {
    const store = useBatchPeakLedger()
    runState.viewingId = 'r-old'
    await store.load('switch')
    expect(get).toHaveBeenLastCalledWith(
      '/batch-peaks/batch/sb-1',
      expect.objectContaining({ params: { min_n_present: 1, batch_peak_run_id: 'r-old' } })
    )
  })
})
