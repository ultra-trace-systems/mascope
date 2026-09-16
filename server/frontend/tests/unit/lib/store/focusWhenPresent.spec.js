import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { shallowRef, ref, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// A record just created reaches its store through a socket creation event,
// which only appends it to the list - no reload. focusWhenPresent has to open
// it from there, whether the event lands before or after the create request
// returns, and must not open it once it has given up waiting.

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
vi.mock('@/stores/auth', () => ({ useAuth: () => ({ user: { user_id: 1 } }) }))

import { useSelection } from '@/lib/store/selection'
import { useEvents } from '@/lib/store/events'

const silentLogger = { debug: vi.fn(), log: vi.fn(), warn: vi.fn(), error: vi.fn() }

let records
let sel
let cleanup
let nextEvent = 0

beforeEach(() => {
  setActivePinia(createPinia())
  handlers.clear()
  records = shallowRef([{ dataset_id: 'ds-1' }])
  sel = useSelection('dataset', 'dataset_id', () => records.value)
  ;({ cleanup } = useEvents(
    'dataset',
    'dataset_id',
    { records, error: ref(null), selection: sel, detailed: null },
    { sync: vi.fn(), reloadRecord: vi.fn() },
    [],
    silentLogger
  ))
})

afterEach(() => {
  cleanup?.()
  vi.useRealTimers()
})

const created = (dataset_id) =>
  handlers.get('dataset_created')({
    event_id: `evt-${++nextEvent}`,
    timestamp: '2026-09-16T00:00:00Z',
    operation: 'created',
    record_id: dataset_id,
    record: { dataset_id }
  })

describe('focusWhenPresent', () => {
  it('opens a record whose creation event arrives after the call', async () => {
    sel.focusWhenPresent({ dataset_id: 'ds-new' })
    expect(sel.focusedId.value).toBeNull()

    created('ds-new')
    await nextTick()

    expect(sel.focusedId.value).toBe('ds-new')
  })

  it('opens a record whose creation event arrived first', () => {
    created('ds-new')

    sel.focusWhenPresent({ dataset_id: 'ds-new' })

    expect(sel.focusedId.value).toBe('ds-new')
  })

  it('does not open the record once it has given up waiting', async () => {
    vi.useFakeTimers()
    sel.focusWhenPresent({ dataset_id: 'ds-new' })

    vi.advanceTimersByTime(61_000)
    created('ds-new')
    await nextTick()

    expect(sel.focusedId.value).toBeNull()
  })

  it('waits only for the latest record asked for', async () => {
    sel.focusWhenPresent({ dataset_id: 'ds-a' })
    sel.focusWhenPresent({ dataset_id: 'ds-b' })

    created('ds-a')
    await nextTick()
    expect(sel.focusedId.value).toBeNull()

    created('ds-b')
    await nextTick()
    expect(sel.focusedId.value).toBe('ds-b')
  })
})
