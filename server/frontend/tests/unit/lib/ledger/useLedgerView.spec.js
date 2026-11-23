import { describe, it, expect, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

import {
  LEDGER_VIEW_STORAGE_KEY,
  defaultLedgerView,
  restoreLedgerView,
  useLedgerListing,
  useLedgerView,
  useLedgerViewStore
} from '@/lib/ledger/useLedgerView.js'

// The one store of how each ledger is viewed: the sort, the search, the chips
// and every Show choice, per scope, and the listing switch both share.

const stored = () => JSON.parse(localStorage.getItem(LEDGER_VIEW_STORAGE_KEY))

// A fresh session: a new Pinia reads the store back from storage.
const reload = () => setActivePinia(createPinia())

beforeEach(() => {
  localStorage.clear()
  reload()
})

describe('useLedgerViewStore', () => {
  it('starts each scope at its defaults', () => {
    const view = useLedgerViewStore()
    expect({ ...view.sample }).toEqual(defaultLedgerView('sample'))
    expect({ ...view.batch }).toEqual(defaultLedgerView('batch'))
    expect(view.sample.tiers).toEqual([])
    expect(view.sample.roles).toBe(true)
    expect(view.batch.badge).toBeNull()
    expect(useLedgerListing().full).toBe(false)
  })

  it('keeps each scope its own choices, through a reload', async () => {
    const sample = useLedgerView('sample')
    const batch = useLedgerView('batch')
    sample.search = 'C6H'
    sample.tiers.push('candidate')
    sample.queue = true
    sample.groupBy = 'compound'
    batch.blends = true
    batch.badge = 'series'
    batch.verdict = 'rejected'
    useLedgerListing().full = true
    await nextTick()

    reload()
    const view = useLedgerViewStore()
    expect(view.sample.search).toBe('C6H')
    expect(view.sample.tiers).toEqual(['candidate'])
    expect(view.sample.queue).toBe(true)
    expect(view.sample.groupBy).toBe('compound')
    expect(view.batch.blends).toBe(true)
    expect(view.batch.badge).toBe('series')
    expect(view.batch.verdict).toBe('rejected')
    expect(useLedgerListing().full).toBe(true)
    // One scope's choice is not the other's.
    expect(view.batch.search).toBe('')
    expect(view.batch.tiers).toEqual([])
    expect(view.sample.verdict).toBe('all')
    expect(stored().listingFull).toBe(true)
  })

  // A stored value the code no longer knows takes its key's default, and the
  // keys beside it keep theirs.
  it('falls back, key by key, on a stored value it does not know', () => {
    localStorage.setItem(
      LEDGER_VIEW_STORAGE_KEY,
      JSON.stringify({
        sample: {
          tiers: ['assigned', 'retired_tier'],
          verdict: 'maybe',
          groupBy: 'series',
          isotopologues: 'yes',
          search: 42,
          order: 2,
          queue: true
        },
        batch: { badge: 'sparkles', groupBy: 'compound', blends: true, field: 7 },
        listingFull: 'yes'
      })
    )
    const view = useLedgerViewStore()
    expect(view.sample.tiers).toEqual([])
    expect(view.sample.verdict).toBe('all')
    // Series is the batch scope's grouping; the sample has none such.
    expect(view.sample.groupBy).toBe('none')
    expect(view.sample.isotopologues).toBe(false)
    expect(view.sample.search).toBe('')
    expect(view.sample.order).toBe(defaultLedgerView('sample').order)
    expect(view.sample.queue).toBe(true)
    expect(view.batch.badge).toBeNull()
    expect(view.batch.groupBy).toBe('none')
    expect(view.batch.field).toBe(defaultLedgerView('batch').field)
    expect(view.batch.blends).toBe(true)
    expect(useLedgerListing().full).toBe(false)
  })

  it('falls back to the defaults on unreadable storage', () => {
    localStorage.setItem(LEDGER_VIEW_STORAGE_KEY, '{not json')
    const view = useLedgerViewStore()
    expect({ ...view.sample }).toEqual(defaultLedgerView('sample'))
    expect({ ...view.batch }).toEqual(defaultLedgerView('batch'))
  })

  // Before this store the listing switch had its own key.
  it("reads the listing switch's old key once the new one is absent", () => {
    localStorage.setItem('mascope.browser.match.ledgerListing', JSON.stringify({ full: true }))
    expect(useLedgerListing().full).toBe(true)
  })

  it('does not read the old keys once the store has written its own', async () => {
    useLedgerListing().full = false
    useLedgerView('batch').field = 'mz'
    await nextTick()
    localStorage.setItem('mascope.browser.match.ledgerListing', JSON.stringify({ full: true }))
    localStorage.setItem(
      'mascope.browser.match.ledgerSort',
      JSON.stringify({ batch: { field: 'max_intensity', order: 1 } })
    )
    reload()
    expect(useLedgerListing().full).toBe(false)
    expect(useLedgerView('batch').field).toBe('mz')
  })
})

describe('restoreLedgerView', () => {
  it('reads a stored list as a copy, so the store never shares the parsed array', () => {
    const tiers = ['assigned']
    const view = restoreLedgerView('batch', { tiers })
    view.tiers.push('candidate')
    expect(tiers).toEqual(['assigned'])
  })

  it('refuses a tier list with a key twice', () => {
    expect(restoreLedgerView('sample', { tiers: ['assigned', 'assigned'] }).tiers).toEqual([])
  })

  it('keeps a cleared sort as one', () => {
    expect(restoreLedgerView('sample', { field: null }).field).toBeNull()
  })

  it('takes the defaults for a stored scope that is no object', () => {
    expect(restoreLedgerView('batch', ['mz'])).toEqual(defaultLedgerView('batch'))
    // A string would throw on `in`.
    expect(restoreLedgerView('batch', 'mz')).toEqual(defaultLedgerView('batch'))
    expect(restoreLedgerView('sample', 7)).toEqual(defaultLedgerView('sample'))
  })
})
