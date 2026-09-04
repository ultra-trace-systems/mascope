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

const RECORDS = [
  {
    sample_item_id: 's-1',
    run: { engine: 'peaky', engine_version: '0.7.0' },
    n_members: 3,
    n_assigned: 2
  },
  { sample_item_id: 's-2', run: null, n_members: 3, n_assigned: 0 }
]

let useBatchPeakSampleStatus
beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  ;({ useBatchPeakSampleStatus } = await import('@/stores/data/modules/batchPeak/sampleStatus'))
})

describe('batchPeak sample status store', () => {
  it("loads the focused batch's sample statuses", async () => {
    get.mockResolvedValue(RECORDS)
    const store = useBatchPeakSampleStatus()
    await store.load('test')
    expect(get).toHaveBeenCalledWith(
      '/batch-peaks/batch/sb-1/sample-status',
      expect.objectContaining({ use: 'read' })
    )
    expect(store.list).toHaveLength(2)
  })

  it('answers per sample, and with nothing for a sample it does not know', async () => {
    get.mockResolvedValue(RECORDS)
    const store = useBatchPeakSampleStatus()
    await store.load('test')
    expect(store.forSample('s-1')).toMatchObject({ n_assigned: 2 })
    expect(store.forSample('s-2')).toMatchObject({ run: null })
    expect(store.forSample('s-9')).toBeNull()
  })
})
