import { describe, it, expect, beforeEach, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// The sample's run list, as far as Assign sample reads it: the button waits
// while a run of the sample is in flight, so the list has to learn that the run
// ended however it ended. The server's peak_assignment_reload comes on success
// only; a failed or cancelled run is learned of from its process's end.

const get = vi.fn()
// The notification store's socket listener, driven by hand below.
let deliver = null
vi.mock('@/api', () => ({
  api: {
    http: { get: (...args) => get(...args), post: vi.fn() },
    socket: {
      on: vi.fn((event, handler) => {
        if (event === 'user_notification') deliver = handler
      }),
      off: vi.fn(),
      addSubscription: vi.fn(),
      removeSubscription: vi.fn()
    }
  }
}))
vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: true }))
vi.mock('@/stores/data/modules/sample', () => ({
  useSample: () => ({ focusedId: 'si-1' })
}))
vi.mock('@/stores/auth', () => ({ useAuth: () => ({ user: {}, onLogin: vi.fn() }) }))

const run = (status) => ({ peak_assignment_run_id: 'run-1', sample_item_id: 'si-1', status })

let usePeakAssignmentRun
let useNotification
beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  deliver = null
  ;({ usePeakAssignmentRun } = await import('@/stores/data/modules/peakAssignment/run'))
  ;({ useNotification } = await import('@/stores/ui/notification'))
})

async function storeHolding(status) {
  // The notification store is the app's: created before the run store asks it.
  useNotification()
  const store = usePeakAssignmentRun()
  get.mockResolvedValueOnce([run(status)])
  await store.load('test')
  get.mockClear()
  return store
}

let seq = 0
async function notify(type, status) {
  seq += 1
  deliver({ id: `n-${seq}`, type, status, process_id: `p-${seq}`, message: '' })
  await flushPromises()
}

describe('peakAssignment run store, a run that ends', () => {
  it.each(['error', 'warning', 'success'])(
    "reads the list again when the sample's run process ends with %s",
    async (status) => {
      const store = await storeHolding('running')
      get.mockResolvedValueOnce([run('failed')])

      await notify('assign_sample_peaks', status)

      expect(get).toHaveBeenCalledTimes(1)
      expect(get.mock.calls[0][0]).toBe('/peak-assignments/sample/si-1/runs')
      expect(store.list[0].status).toBe('failed')
    }
  )

  it('reads it again when the batch pass that ran it ends', async () => {
    await storeHolding('pending')
    get.mockResolvedValueOnce([run('cancelled')])

    await notify('assign_batch', 'error')

    expect(get).toHaveBeenCalledTimes(1)
  })

  it('reads nothing on a progress packet', async () => {
    await storeHolding('running')

    await notify('assign_sample_peaks', 'pending')

    expect(get).not.toHaveBeenCalled()
  })

  it('reads nothing when no run in the list is in flight', async () => {
    await storeHolding('completed')

    await notify('assign_sample_peaks', 'error')

    expect(get).not.toHaveBeenCalled()
  })

  it('reads nothing for another kind of process', async () => {
    await storeHolding('running')

    await notify('rematch_sample', 'error')

    expect(get).not.toHaveBeenCalled()
  })
})
