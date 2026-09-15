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
vi.mock('@/stores/auth', () => ({ useAuth: () => ({ user: {}, onLogin: vi.fn() }) }))

/** Runs newest first, as the route serves them. */
const run = (id, action, over = {}) => ({
  batch_peak_run_id: id,
  sample_batch_id: 'sb-1',
  action,
  engine: 'mascope',
  engine_version: '1.0.0',
  status: 'completed',
  current: false,
  batch_peak_run_utc_created: '2026-09-04T10:00:00Z',
  ...over
})
const SEARCH = run('r-search', 'search_untargeted', { current: true })
const FOLD = run('r-fold', 'fold', { batch_peak_run_utc_created: '2026-09-04T09:00:00Z' })

let useBatchPeakRun
beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  ;({ useBatchPeakRun } = await import('@/stores/data/modules/batchPeak/run'))
})

async function loaded(records) {
  get.mockResolvedValue(records)
  const store = useBatchPeakRun()
  await store.load('test')
  return store
}

describe('batchPeak run store', () => {
  it("loads the focused batch's runs", async () => {
    await loaded([SEARCH, FOLD])
    expect(get).toHaveBeenCalledWith(
      '/batch-peaks/batch/sb-1/runs',
      expect.objectContaining({ use: 'read' })
    )
  })

  it('follows the current run, which is the live ledger', async () => {
    const store = await loaded([SEARCH, FOLD])
    expect(store.current?.batch_peak_run_id).toBe('r-search')
    expect(store.focusedId).toBe('r-search')
    expect(store.viewingCurrent).toBe(true)
    expect(store.viewingId).toBeNull()
  })

  it('names an earlier run for the reads when the user picks one', async () => {
    const store = await loaded([SEARCH, FOLD])
    store.focus(FOLD)
    expect(store.viewingCurrent).toBe(false)
    expect(store.viewingId).toBe('r-fold')
  })

  it('moves to a new current run when one completes, leaving a chosen older run alone otherwise', async () => {
    const store = await loaded([SEARCH, FOLD])
    store.focus(FOLD)
    // A reload with the same current keeps the user's choice...
    get.mockResolvedValue([SEARCH, FOLD])
    await store.load('reload')
    expect(store.focusedId).toBe('r-fold')
    // ... a new current run takes the view with it.
    const REBUILD = run('r-rebuild', 'rebuild', { current: true })
    get.mockResolvedValue([REBUILD, { ...SEARCH, current: false }, FOLD])
    await store.load('completed')
    expect(store.focusedId).toBe('r-rebuild')
    expect(store.viewingId).toBeNull()
  })

  it('reads as the live ledger when the batch has no runs yet', async () => {
    const store = await loaded([])
    expect(store.current).toBeNull()
    expect(store.viewingCurrent).toBe(true)
    expect(store.viewingId).toBeNull()
  })
})
