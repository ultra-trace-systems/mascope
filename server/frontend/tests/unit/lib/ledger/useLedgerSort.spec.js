import { describe, it, expect, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

import {
  DEFAULT_LEDGER_SORT,
  compareBy,
  useLedgerSort,
  useLedgerSortStore
} from '@/lib/ledger/useLedgerSort.js'

const KEY = 'mascope.browser.match.ledgerSort'

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})

describe('useLedgerSortStore', () => {
  it('starts each ledger at its default sort', () => {
    const sort = useLedgerSortStore()
    expect({ ...sort.sample }).toEqual(DEFAULT_LEDGER_SORT.sample)
    expect({ ...sort.batch }).toEqual(DEFAULT_LEDGER_SORT.batch)
  })

  it('hands a change to the next pane that asks, and writes it through to storage', async () => {
    const sort = useLedgerSortStore()
    sort.sample.field = 'sample_peak_intensity'
    sort.sample.order = -1
    await nextTick()

    // The remounted pane gets the same store, so the same sort.
    expect(useLedgerSortStore().sample.field).toBe('sample_peak_intensity')
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
    const sort = useLedgerSortStore()
    expect(sort.sample.field).toBeNull()
    expect(sort.sample.order).toBe(1)
    expect(sort.batch.field).toBe('mz')
    expect(sort.batch.order).toBe(DEFAULT_LEDGER_SORT.batch.order)
  })

  it('falls back to the defaults on unreadable storage', () => {
    localStorage.setItem(KEY, '{not json')
    const sort = useLedgerSortStore()
    expect({ ...sort.sample }).toEqual(DEFAULT_LEDGER_SORT.sample)
    expect({ ...sort.batch }).toEqual(DEFAULT_LEDGER_SORT.batch)
  })
})

describe('compareBy', () => {
  const rows = [{ f: 'C10H22' }, { f: null }, { f: 'C2H6' }, { f: '' }, { f: 'C3H8' }]

  it('collates numerically, so C2H6 reads before C10H22', () => {
    expect(
      rows
        .slice()
        .sort(compareBy('f', 1))
        .map((row) => row.f)
    ).toEqual(['C2H6', 'C3H8', 'C10H22', null, ''])
  })

  // A peak with no formula is unknown, not "smallest": it sorts last in both
  // directions, the empty string with it.
  it('sorts a blank value last in both directions', () => {
    const descending = rows
      .slice()
      .sort(compareBy('f', -1))
      .map((row) => row.f)
    expect(descending.slice(0, 3)).toEqual(['C10H22', 'C3H8', 'C2H6'])
    expect(descending.slice(3).every((value) => value == null || value === '')).toBe(true)
  })

  it('reads the value through the given reader', () => {
    const compare = compareBy('shown', 1, (row) => row.alias)
    expect(compare({ alias: 2 }, { alias: 1 })).toBe(1)
  })
})

describe('useLedgerSort', () => {
  const byName = (a, b) => a.name.localeCompare(b.name)

  it("binds a scope's field and order to the store", () => {
    const sort = useLedgerSort('batch')
    sort.field.value = 'mz'
    sort.order.value = 1
    expect(useLedgerSortStore().batch).toEqual({ field: 'mz', order: 1 })
    // The other scope keeps its own.
    expect({ ...useLedgerSortStore().sample }).toEqual(DEFAULT_LEDGER_SORT.sample)
  })

  it('sorts by the column, the resting order breaking ties, and rests while none is chosen', () => {
    const sort = useLedgerSort('sample', { shown: (row) => row.value })
    const rows = () => [
      { name: 'b', value: 1 },
      { name: 'a', value: 1 },
      { name: 'c', value: 0 }
    ]
    sort.field.value = 'shown'
    sort.order.value = -1
    expect(sort.sort(rows(), byName).map((row) => row.name)).toEqual(['a', 'b', 'c'])
    sort.field.value = null
    expect(sort.sort(rows(), byName).map((row) => row.name)).toEqual(['a', 'b', 'c'])
    sort.field.value = 'shown'
    sort.order.value = 1
    expect(sort.sort(rows(), byName).map((row) => row.name)).toEqual(['c', 'a', 'b'])
  })
})
