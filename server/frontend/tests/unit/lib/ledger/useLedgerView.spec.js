import { describe, it, expect, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

import {
  LEDGER_VIEW_STORAGE_KEY,
  LEDGER_VIEW_VERSION,
  SORT_FIELDS,
  defaultLedgerView,
  restoreLedgerView,
  upgradeLedgerView,
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
        batch: { badge: 'sparkles', groupBy: 'family', blends: true, field: 7 },
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
    // The batch groups by compound or series; a family is the fold, no grouping.
    expect(view.batch.groupBy).toBe('none')
    expect(view.batch.field).toBe(defaultLedgerView('batch').field)
    expect(view.batch.blends).toBe(true)
    expect(useLedgerListing().full).toBe(false)
  })

  it('keeps a batch grouping by compound or by series', () => {
    for (const groupBy of ['compound', 'series']) {
      setActivePinia(createPinia())
      localStorage.setItem(LEDGER_VIEW_STORAGE_KEY, JSON.stringify({ batch: { groupBy } }))
      expect(useLedgerViewStore().batch.groupBy).toBe(groupBy)
    }
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

  // A column dropped since, or the other scope's: the scope's default sort,
  // not a sort on nothing.
  it("takes the scope's default sort for a column it does not have", () => {
    expect(restoreLedgerView('batch', { field: 'retired_rank' }).field).toBe('max_intensity')
    expect(restoreLedgerView('sample', { field: 'retired_rank' }).field).toBe(
      'sample_peak_intensity'
    )
    expect(restoreLedgerView('sample', { field: 'n_present' }).field).toBe('sample_peak_intensity')
    expect(restoreLedgerView('batch', { field: 'sample_peak_mz' }).field).toBe('max_intensity')
    // The columns this build dropped: the listing, the engine's, the batch's
    // reading columns. A sort stored on one falls back.
    for (const field of ['listing', 'engineTierRank']) {
      expect(restoreLedgerView('sample', { field }).field).toBe('sample_peak_intensity')
    }
    for (const field of ['listingName', 'continuityRank', 'coVariationRank', 'checksRank']) {
      expect(restoreLedgerView('batch', { field }).field).toBe('max_intensity')
    }
    // Every column it lists is kept.
    for (const scope of ['sample', 'batch']) {
      for (const field of SORT_FIELDS[scope]) {
        expect(restoreLedgerView(scope, { field }).field).toBe(field)
      }
    }
  })

  // Owner's decision of 2026-11-22: intensity, descending, in both scopes.
  it('sorts both scopes by intensity, brightest first, by default', () => {
    expect(defaultLedgerView('sample')).toMatchObject({
      field: 'sample_peak_intensity',
      order: -1
    })
    expect(defaultLedgerView('batch')).toMatchObject({ field: 'max_intensity', order: -1 })
  })

  it('takes the defaults for a stored scope that is no object', () => {
    expect(restoreLedgerView('batch', ['mz'])).toEqual(defaultLedgerView('batch'))
    // A string would throw on `in`.
    expect(restoreLedgerView('batch', 'mz')).toEqual(defaultLedgerView('batch'))
    expect(restoreLedgerView('sample', 7)).toEqual(defaultLedgerView('sample'))
  })
})

// A view saved before version 2 holds the former default sort as if chosen:
// every key is saved. It is read as no choice, so the new default applies;
// any other sort a reader chose is kept, and so is everything beside it.
describe('the upgrade to the intensity default', () => {
  it('drops the former default sort of a view saved before the version', () => {
    localStorage.setItem(
      LEDGER_VIEW_STORAGE_KEY,
      JSON.stringify({
        sample: { field: 'tierRank', order: 1, search: 'C2' },
        batch: { field: 'n_present', order: -1, roles: false }
      })
    )
    const view = useLedgerViewStore()
    expect(view.sample).toMatchObject({ field: 'sample_peak_intensity', order: -1, search: 'C2' })
    expect(view.batch).toMatchObject({ field: 'max_intensity', order: -1, roles: false })
  })

  it('keeps a sort the reader chose, and one saved at the version', async () => {
    localStorage.setItem(
      LEDGER_VIEW_STORAGE_KEY,
      JSON.stringify({
        sample: { field: 'tierRank', order: -1 },
        batch: { field: 'mz', order: 1 }
      })
    )
    let view = useLedgerViewStore()
    expect(view.sample).toMatchObject({ field: 'tierRank', order: -1 })
    expect(view.batch).toMatchObject({ field: 'mz', order: 1 })

    // Saved by this build, the tier sort is the reader's choice.
    view.sample.order = 1
    await nextTick()
    expect(stored().version).toBe(LEDGER_VIEW_VERSION)
    reload()
    view = useLedgerViewStore()
    expect(view.sample).toMatchObject({ field: 'tierRank', order: 1 })
  })

  it('upgrades the scope it is given and nothing else', () => {
    expect(upgradeLedgerView('sample', { field: 'n_present', order: -1 }, undefined)).toEqual({
      field: 'n_present',
      order: -1
    })
    expect(upgradeLedgerView('batch', { field: 'n_present', order: -1 }, 1)).toEqual({})
    expect(upgradeLedgerView('batch', { field: 'n_present', order: -1 }, 2)).toEqual({
      field: 'n_present',
      order: -1
    })
    expect(upgradeLedgerView('batch', null, undefined)).toBeNull()
  })
})
