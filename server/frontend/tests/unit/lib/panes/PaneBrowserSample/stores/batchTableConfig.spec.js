import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { reactive, nextTick } from 'vue'
import { createPinia, setActivePinia, disposePinia } from 'pinia'

// The store reaches into useApp() only for the focused dataset id, which forms
// the localStorage key; a stub keeps the rest of the app store graph out.
const { app } = vi.hoisted(() => ({ app: { current: null } }))
vi.mock('@/stores', () => ({ useApp: () => app.current }))

import { useBatchTableConfig } from '@/lib/panes/PaneBrowserSample/stores/batchTableConfig'

const KEY = 'sample-browser-dataset[ds-1]'

let pinia = null

// A page load: a fresh pinia re-runs the store setup (and its restore), while
// localStorage survives. Disposing the previous one retires its watchers, the
// way closing the page would.
const load = () => {
  if (pinia) disposePinia(pinia)
  pinia = createPinia()
  setActivePinia(pinia)
  return useBatchTableConfig()
}

describe('useBatchTableConfig persistence', () => {
  beforeEach(() => {
    localStorage.clear()
    app.current = reactive({ data: { dataset: { focusedId: 'ds-1' } } })
  })

  afterEach(() => {
    if (pinia) disposePinia(pinia)
    pinia = null
  })

  it('persists a non-default sort order across a reload', async () => {
    const table = load()
    table.config.sortOrder = 1
    await nextTick()

    expect(load().config.sortOrder).toBe(1)
  })

  // Regression test for the batch sorting that would not stick (#1391).
  it('restores the default sort order after sorting back to it', async () => {
    const table = load()
    table.config.sortOrder = 1 // ascending - stored
    await nextTick()

    const reopened = load()
    reopened.config.sortOrder = -1 // back to descending, no search term typed
    await nextTick()

    // Before the fix the stale ascending entry survived here and was restored.
    expect(localStorage.getItem(KEY)).toBeNull()
    expect(load().config.sortOrder).toBe(-1)
  })

  it('persists sorting without needing a search term first', async () => {
    // The workaround from the issue: a search term kept the config away from
    // the default, which is what made the write happen at all.
    const table = load()
    table.config.filters.global.value = 'blank'
    table.config.sortOrder = 1
    await nextTick()

    const reopened = load()
    expect(reopened.config.sortOrder).toBe(1)
    expect(reopened.config.filters.global.value).toBe('blank')
  })

  it('keys the stored config by dataset', async () => {
    const table = load()
    table.config.sortOrder = 1
    await nextTick()

    app.current.data.dataset.focusedId = 'ds-2'
    await nextTick()
    expect(table.config.sortOrder).toBe(-1) // nothing stored for ds-2

    app.current.data.dataset.focusedId = 'ds-1'
    await nextTick()
    expect(table.config.sortOrder).toBe(1)
  })
})
