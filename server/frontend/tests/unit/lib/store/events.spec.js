import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { ref, shallowRef } from 'vue'

// Socket handlers are captured as the store registers them, so a test can fire
// a broadcast at exactly the handler the app would receive it on.
const handlers = new Map()

vi.mock('@/api', () => ({
  api: {
    socket: {
      on: (event, handler) => handlers.set(event, handler),
      off: (event) => handlers.delete(event),
      addSubscription: vi.fn(),
      removeSubscription: vi.fn()
    }
  }
}))

let authUser
vi.mock('@/stores/auth', () => ({ useAuth: () => ({ user: authUser }) }))

import { useEvents } from '@/lib/store/events'

const silentLogger = { debug: vi.fn(), log: vi.fn(), warn: vi.fn(), error: vi.fn() }

let cleanup
let sync
let reloadRecord
let records
let error

/** Wire useEvents the way useData does. */
const wire = () => {
  records = shallowRef([{ test_id: 'a', index: '1' }])
  error = ref(null)
  sync = vi.fn(async () => ({ ok: true, error: null, superseded: false }))
  reloadRecord = vi.fn(async () => null)
  ;({ cleanup } = useEvents(
    'test',
    'test_id',
    { records, error, selection: null, detailed: null },
    { sync, reloadRecord },
    [],
    silentLogger
  ))
}

let nextEvent = 0
// A fresh id each time: the backend mints one per broadcast, so the store's
// dedup cache never suppresses a repeat.
const fire = (operation, extra = {}) =>
  handlers.get(`test_${operation}`)({
    event_id: `evt-${++nextEvent}`,
    timestamp: '2026-08-21T00:00:00Z',
    operation,
    record_id: 'b',
    record: { test_id: 'b' },
    ...extra
  })

beforeEach(() => {
  handlers.clear()
  vi.clearAllMocks()
  authUser = { user_id: 1 }
  wire()
})

afterEach(() => cleanup?.())

describe('useEvents record broadcasts', () => {
  it('patches the rows when the store is healthy', () => {
    fire('created')

    expect(sync).not.toHaveBeenCalled()
    expect(records.value.map((r) => r.test_id)).toEqual(['a', 'b'])
  })

  // Patching one record into rows we could not refresh would neither make the
  // list truthful nor lift the error the pane is showing in place of it.
  it('re-syncs instead of patching once a load has failed', () => {
    error.value = new Error('503')
    fire('created')

    expect(sync).toHaveBeenCalledTimes(1)
    expect(records.value.map((r) => r.test_id)).toEqual(['a'])
  })

  // The regression this file exists for. Reload broadcasts reach every connected
  // socket, including tabs sitting on the login screen - and a session that
  // expires mid-use latches `error`, so without this gate every later broadcast
  // fetched again, failed again, and re-toasted "session expired" on a screen
  // that never had a session. Self-sustaining, because the failure re-latches.
  it('does not fetch from a signed-out tab whose last load failed', async () => {
    error.value = new Error('401')
    authUser = false

    await fire('reload')
    await fire('created')
    await fire('updated')

    expect(sync).not.toHaveBeenCalled()
  })

  it('does not fetch while the user is held at the password gate', async () => {
    error.value = new Error('401')
    authUser = { user_id: 1, must_change_password: true }

    await fire('reload')
    await fire('created')

    expect(sync).not.toHaveBeenCalled()
  })

  it('still reloads for a signed-in tab', async () => {
    await fire('reload')

    expect(sync).toHaveBeenCalledTimes(1)
    expect(reloadRecord).toHaveBeenCalledTimes(1)
  })

  // A failed sync used to reject and take the reload down with it; now that it
  // resolves, the skip has to be explicit or the reload asks the endpoint that
  // just refused and toasts the same failure twice.
  it('skips the record reload when the sync failed', async () => {
    sync.mockResolvedValue({ ok: false, error: new Error('boom'), superseded: false })

    await fire('reload')

    expect(sync).toHaveBeenCalledTimes(1)
    expect(reloadRecord).not.toHaveBeenCalled()
  })
})
