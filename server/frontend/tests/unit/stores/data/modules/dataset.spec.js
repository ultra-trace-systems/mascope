import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// A dataset refresh fans out over every batch in the dataset, so the defaults
// it posts with matter: `full_remove`/`force` off keeps it the batch-level
// "Refresh matches" repeated, which skips batches that are already matched.
// Defaulting either one on would silently rebuild every match in the dataset.

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

vi.mock('@/stores/data/modules/workspace', () => ({
  useWorkspace: () => ({ focusedId: 'ws-1' })
}))

let useDataset

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  ;({ useDataset } = await import('@/stores/data/modules/dataset'))
})

describe('dataset store: rematch', () => {
  it('asks the match API to refresh the dataset without forcing a full rematch', async () => {
    useDataset().rematch({ dataset_id: 'ds-2' })

    const [url, body, config] = post.mock.calls[0]
    expect(url).toBe('/match/rematch/dataset/ds-2')
    expect(body).toEqual({})
    expect(config.params).toEqual({ full_remove: false, force: false })
    expect(config.use).toBe('process')
  })
})
