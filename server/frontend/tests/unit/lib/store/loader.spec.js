import { describe, it, expect, beforeEach, vi } from 'vitest'
import { ref, shallowRef, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// selection.js reaches the socket API through its `subscribe` option, which
// these tests do not exercise; a stub keeps the import graph happy.
vi.mock('@/api', () => ({
  api: {
    socket: { addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))

import { useLoader } from '@/lib/store/loader'
import { useSelection } from '@/lib/store/selection'

const silentLogger = { debug: vi.fn(), log: vi.fn(), warn: vi.fn(), error: vi.fn() }

/**
 * Build a loader over the given fetch method, mirroring how useData wires it.
 */
const makeLoader = (method, { deps = null, selection = null } = {}) => {
  const refs = {
    records: shallowRef([]),
    pending: ref(false),
    error: ref(null),
    selection,
    detailed: null
  }
  const loader = useLoader('test', 'test_id', method, refs, { deps, read: null }, silentLogger)
  return { ...refs, ...loader }
}

describe('useLoader sync', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('clears pending after a successful sync', async () => {
    const store = makeLoader(async () => [{ test_id: 'a' }])

    await store.load('initialization')

    expect(store.pending.value).toBe(false)
    expect(store.error.value).toBeNull()
    expect(store.records.value).toHaveLength(1)
  })

  // The regression this file exists for: a rejected fetch used to abort sync()
  // before the flag was cleared, leaving every consuming pane rendering its
  // spinner for the rest of the session, with only a toast to explain it.
  it('clears pending when the fetch rejects, so the pane cannot latch its spinner', async () => {
    const store = makeLoader(async () => {
      throw new Error('request failed')
    })

    await store.load('initialization')

    expect(store.pending.value).toBe(false)
  })

  it('records the failure instead of rethrowing it', async () => {
    const failure = new Error('503 Service Unavailable')
    const store = makeLoader(async () => {
      throw failure
    })

    // Must not reject: the callers are watchers and lifecycle hooks that cannot catch.
    await expect(store.load('initialization')).resolves.toEqual({
      ok: false,
      error: failure,
      superseded: false
    })
    expect(store.error.value).toBe(failure)
  })

  // A caller that needs its own outcome must not read the shared `error` ref,
  // which any concurrent sync clears or fills in.
  it('reports its own outcome to the caller', async () => {
    const store = makeLoader(async () => [{ test_id: 'a' }])

    await expect(store.load('initialization')).resolves.toEqual({
      ok: true,
      error: null,
      superseded: false
    })
  })

  // Clearing the rows here would drive refocus() against an empty list, unfocus
  // the selection and delete its persisted state - so a transient 500 would cost
  // the user their place, and cascade into every child store. Stale rows are
  // truthful where an empty list would read as "this sample has none".
  it('keeps the rows on failure so the selection survives', async () => {
    let shouldFail = false
    const store = makeLoader(async () => {
      if (shouldFail) throw new Error('boom')
      return [{ test_id: 'a' }, { test_id: 'b' }]
    })

    await store.load('initialization')
    expect(store.records.value).toHaveLength(2)

    shouldFail = true
    await store.load('dependencies')

    expect(store.records.value).toHaveLength(2)
    expect(store.error.value).toBeInstanceOf(Error)
    expect(store.pending.value).toBe(false)
  })

  it('keeps a persisted selection across a failed sync', async () => {
    localStorage.clear()
    const records = shallowRef([])
    const selection = useSelection('test', 'test_id', () => records.value, { persist: true })

    let shouldFail = false
    const refs = {
      records,
      pending: ref(false),
      error: ref(null),
      selection,
      detailed: null
    }
    const loader = useLoader(
      'test',
      'test_id',
      async () => {
        if (shouldFail) throw new Error('boom')
        return [{ test_id: 'a' }, { test_id: 'b' }]
      },
      refs,
      { deps: null, read: null },
      silentLogger
    )

    await loader.load('initialization')
    selection.focus({ test_id: 'b' })
    await nextTick()
    expect(JSON.parse(localStorage.getItem('module[test]'))).toEqual(['b'])

    shouldFail = true
    await loader.load('dependencies')
    await nextTick()

    expect(selection.focusedId.value).toBe('b')
    expect(JSON.parse(localStorage.getItem('module[test]'))).toEqual(['b'])
  })

  // Syncs settle in completion order, not start order, so the store must ignore
  // any result that is no longer the newest - otherwise a slow failure lands on
  // top of a newer success and the pane shows an error over data it already has.
  it('does not let a slow failure clobber a newer success', async () => {
    const gates = []
    const store = makeLoader(
      () =>
        new Promise((resolve, reject) => {
          gates.push({ resolve, reject })
        })
    )

    const slow = store.load('retry') // generation 1
    const fast = store.load('socket event') // generation 2

    gates[1].resolve([{ test_id: 'fresh' }])
    await fast
    expect(store.records.value).toHaveLength(1)
    expect(store.error.value).toBeNull()

    gates[0].reject(new Error('slow boom'))

    // The discarded call says so, rather than reporting a failure the store
    // never adopted.
    await expect(slow).resolves.toEqual({ ok: false, error: null, superseded: true })
    expect(store.error.value).toBeNull()
    expect(store.records.value).toEqual([expect.objectContaining({ test_id: 'fresh' })])
    expect(store.pending.value).toBe(false)
  })

  it('does not let a slow success clobber a newer result', async () => {
    const gates = []
    const store = makeLoader(
      () =>
        new Promise((resolve) => {
          gates.push(resolve)
        })
    )

    const first = store.load('initialization')
    const second = store.load('dependencies')

    // Resolve the newer one first, then the older.
    gates[1]([{ test_id: 'newer' }])
    await second
    gates[0]([{ test_id: 'older' }, { test_id: 'older2' }])
    await first

    expect(store.records.value).toEqual([expect.objectContaining({ test_id: 'newer' })])
    expect(store.pending.value).toBe(false)
  })

  it('clears a previous error once a later sync succeeds', async () => {
    let shouldFail = true
    const store = makeLoader(async () => {
      if (shouldFail) throw new Error('boom')
      return [{ test_id: 'a' }]
    })

    await store.load('initialization')
    expect(store.error.value).toBeInstanceOf(Error)

    shouldFail = false
    await store.load('retry')

    expect(store.error.value).toBeNull()
    expect(store.records.value).toHaveLength(1)
  })

  it('leaves the focused record focused when a sync fails', async () => {
    const records = shallowRef([{ test_id: 'a' }, { test_id: 'b' }])
    const selection = useSelection('test', 'test_id', () => records.value, {})
    selection.focus({ test_id: 'b' })

    const loader = useLoader(
      'test',
      'test_id',
      async () => {
        throw new Error('boom')
      },
      { records, pending: ref(false), error: ref(null), selection, detailed: null },
      { deps: null, read: null },
      silentLogger
    )

    await loader.load('dependencies')

    expect(selection.focusedId.value).toBe('b')
    expect(records.value).toHaveLength(2)
  })

  it('leaves unmet dependencies as an empty list, not an error', async () => {
    const method = vi.fn(async () => [{ test_id: 'a' }])
    const store = makeLoader(method, { deps: () => ({ sample_item_id: null }) })

    await store.load('initialization')

    expect(method).not.toHaveBeenCalled()
    expect(store.records.value).toEqual([])
    expect(store.error.value).toBeNull()
    expect(store.pending.value).toBe(false)
  })

  // refocus() restores the selection and so reaches localStorage. A browser that
  // refuses storage would otherwise turn every successful load into a permanent
  // "could not load this list" that the retry button can never clear, because
  // the throw is deterministic.
  it('does not report a state-restoration fault as a failed load', async () => {
    const selection = {
      prepRefocus: () => () => {
        throw new Error('localStorage is disabled')
      }
    }
    const store = makeLoader(async () => [{ test_id: 'a' }], { selection })

    const outcome = await store.load('initialization')

    expect(outcome.ok).toBe(true)
    expect(store.error.value).toBeNull()
    expect(store.records.value).toHaveLength(1)
    expect(store.pending.value).toBe(false)
    expect(silentLogger.error).toHaveBeenCalled()
  })
})
