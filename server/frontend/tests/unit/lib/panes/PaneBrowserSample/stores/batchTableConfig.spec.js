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

// Only 'restores the default sort order after sorting back to it' is a
// regression test: it is the one case here that fails on the pre-fix store,
// where writeConfig skipped the write for a default config without clearing the
// stale entry an earlier non-default one had left behind (#1391). The other
// three are baseline coverage for a store that had no tests at all - they pass
// before and after the fix, and are here to pin behaviour the fix must not
// disturb.
describe('useBatchTableConfig persistence', () => {
  beforeEach(() => {
    localStorage.clear()
    app.current = reactive({ data: { dataset: { focusedId: 'ds-1' } } })
  })

  afterEach(() => {
    if (pinia) disposePinia(pinia)
    pinia = null
  })

  // No search term is ever set here: an ascending sort is on its own already
  // unequal to the default, so it is stored and restored without the filter
  // value the issue's workaround relied on. That held before the fix too -
  // this is the baseline the regression case below builds on.
  it('persists a non-default sort order with no search term set', async () => {
    const table = load()
    table.config.sortOrder = 1
    await nextTick()

    const reopened = load()
    expect(reopened.config.sortOrder).toBe(1)
    expect(reopened.config.filters.global.value).toBeNull()
  })

  // The #1391 regression test for batch sorting that would not stick - the only
  // case in this file that fails without the fix.
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

  // A search term and a non-default sort travelling together - the shape the
  // issue's workaround produced, since a filter value on its own already keeps
  // the config unequal to the default.
  it('persists a search term alongside a non-default sort order', async () => {
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
