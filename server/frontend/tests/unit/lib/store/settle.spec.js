import { describe, it, expect, afterEach, vi } from 'vitest'
import { ref, nextTick } from 'vue'

import { untilStoreSettled, STORE_SETTLE_TIMEOUT } from '@/lib/store/settle'

// The guard behind every join that has to outlast a store reload. Its three
// cases are the reason it is a shared helper rather than a line of code at each
// call site: returning too early joins against the previous parent's records,
// and never returning strands the caller for good.

const settledFlag = (promise) => {
  let settled = false
  promise.then(() => (settled = true))
  return () => settled
}

afterEach(() => {
  vi.useRealTimers()
})

describe('untilStoreSettled', () => {
  it('returns once a reload that was queued during the tick has finished', async () => {
    const pending = ref(false)
    // A dependency watcher has not run yet, so `pending` is still false at the
    // moment of the switch. Waiting one tick is what lets it be seen at all.
    const done = untilStoreSettled(() => pending.value)
    const isSettled = settledFlag(done)
    pending.value = true

    await nextTick()
    await nextTick()
    expect(isSettled()).toBe(false)

    pending.value = false
    await done
    expect(isSettled()).toBe(true)
  })

  it('returns straight away when nothing is loading', async () => {
    const pending = ref(false)

    await untilStoreSettled(() => pending.value)

    expect(pending.value).toBe(false)
  })

  it('gives up after the backstop rather than waiting for good', async () => {
    vi.useFakeTimers()
    const pending = ref(true)

    const done = untilStoreSettled(() => pending.value)
    const isSettled = settledFlag(done)

    await vi.advanceTimersByTimeAsync(STORE_SETTLE_TIMEOUT - 1)
    expect(isSettled()).toBe(false)

    await vi.advanceTimersByTimeAsync(2)
    // It RESOLVES rather than throws, so a caller has nothing to catch -- and
    // nothing to tell it the wait failed except `pending` still being true.
    await done
    expect(isSettled()).toBe(true)
    expect(pending.value).toBe(true)
  })
})
