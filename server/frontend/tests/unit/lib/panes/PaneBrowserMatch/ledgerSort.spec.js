import { describe, it, expect, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

import {
  useLedgerSort,
  DEFAULT_LEDGER_SORT
} from '@/lib/panes/PaneBrowserMatch/stores/ledgerSort.js'

const KEY = 'mascope.browser.match.ledgerSort'

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})

describe('useLedgerSort', () => {
  it('starts each ledger at its default sort', () => {
    const sort = useLedgerSort()
    expect({ ...sort.sample }).toEqual(DEFAULT_LEDGER_SORT.sample)
    expect({ ...sort.batch }).toEqual(DEFAULT_LEDGER_SORT.batch)
  })

  it('hands a change to the next pane that asks, and writes it through to storage', async () => {
    const sort = useLedgerSort()
    sort.sample.field = 'sample_peak_intensity'
    sort.sample.order = -1
    await nextTick()

    // The remounted pane gets the same store, so the same sort.
    expect(useLedgerSort().sample.field).toBe('sample_peak_intensity')
    expect(JSON.parse(localStorage.getItem(KEY))).toEqual({
      sample: { field: 'sample_peak_intensity', order: -1 },
      batch: { ...DEFAULT_LEDGER_SORT.batch }
    })
    // The other ledger's sort is untouched by it.
    expect({ ...sort.batch }).toEqual(DEFAULT_LEDGER_SORT.batch)
  })

  // A new session reads the sort back. A cleared sort (removableSort's third
  // click leaves the field null) is a choice too, and comes back as one rather
  // than as the default column; a key that was never stored falls back.
  it('restores a stored sort in a fresh session, a cleared sort included', () => {
    localStorage.setItem(
      KEY,
      JSON.stringify({ sample: { field: null, order: 1 }, batch: { field: 'mz' } })
    )
    const sort = useLedgerSort()
    expect(sort.sample.field).toBeNull()
    expect(sort.sample.order).toBe(1)
    expect(sort.batch.field).toBe('mz')
    expect(sort.batch.order).toBe(DEFAULT_LEDGER_SORT.batch.order)
  })

  it('falls back to the defaults on unreadable storage', () => {
    localStorage.setItem(KEY, '{not json')
    const sort = useLedgerSort()
    expect({ ...sort.sample }).toEqual(DEFAULT_LEDGER_SORT.sample)
    expect({ ...sort.batch }).toEqual(DEFAULT_LEDGER_SORT.batch)
  })
})
