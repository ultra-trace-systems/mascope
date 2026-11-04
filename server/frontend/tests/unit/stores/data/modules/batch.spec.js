import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// Moving a batch answers in the one request, with the moved batch, so it goes
// through the update handler (a success toast) rather than the background
// process one, which would wait for a notification that never comes.

const post = vi.fn()
const get = vi.fn()

vi.mock('@/api', () => ({
  api: {
    http: {
      post: (...args) => post(...args),
      get: (...args) => get(...args)
    },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))

vi.mock('@/stores/data/modules/dataset', () => ({
  useDataset: () => ({ focusedId: 'ds-1' })
}))

let useBatch

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  ;({ useBatch } = await import('@/stores/data/modules/batch'))
})

describe('batch store: move', () => {
  it('posts the target dataset to the batch move route', async () => {
    useBatch().move({ sample_batch_id: 'b-1', dataset_id: 'ds-2' })

    const [url, body, config] = post.mock.calls[0]
    expect(url).toBe('/sample/batches/b-1/move')
    expect(body).toEqual({ dataset_id: 'ds-2' })
    expect(config.use).toBe('update')
    expect(config.type).toBe('move_batch')
  })
})
