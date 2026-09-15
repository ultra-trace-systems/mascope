import { describe, it, expect, beforeEach, vi } from 'vitest'
import { reactive, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// The wiring around the follower: which (sample, peak) pair a switch is
// anchored on, and when a switch is not a follow at all. The follower's own
// ordering rules are covered in peakFocusFollow.spec.js.

const get = vi.fn()
const post = vi.fn()

vi.mock('@/api', () => ({
  api: {
    http: {
      get: (...args) => get(...args),
      post: (...args) => post(...args)
    },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))

// `peakAssignmentEnabled` is a const read at module load, so the flag has to be
// reachable through a live getter for a test to be able to turn it off.
const features = vi.hoisted(() => ({ on: true }))
vi.mock('@/lib/features', () => ({
  get peakAssignmentEnabled() {
    return features.on
  },
  maxUploadBytes: 5 * 1024 ** 3
}))

// The parent stores are hand-driven. They are built in beforeEach (the mock
// factories only run when peak.js is imported, which happens after it), so each
// test gets its own reactive state rather than one shared across the file.
const parents = vi.hoisted(() => ({ sample: null, batch: null, ledger: null }))
vi.mock('@/stores/data/modules/sample', () => ({ useSample: () => parents.sample }))
vi.mock('@/stores/data/modules/batch', () => ({ useBatch: () => parents.batch }))
vi.mock('@/stores/data/modules/batchPeak', () => ({
  useBatchPeakLedger: () => parents.ledger
}))

// This spec boots the real store graph and drives it through several async
// reloads per test, so it is the slowest kind of unit test in the suite. The
// defaults are budgeted for something much cheaper and start failing on a
// loaded machine when the whole suite runs in parallel.
vi.setConfig({ testTimeout: 20_000 })
const WAIT = { timeout: 10_000, interval: 10 }

/** The peaks endpoint answers in the columnar shape the store unpacks. */
const peaksOf = (...ids) => ({
  peak_id: ids,
  mz: ids.map((_, i) => 100 + i),
  area: ids.map(() => 1),
  height: ids.map(() => 1),
  match: ids.map(() => null)
})

let store
let sample
let ledger
let batch

/**
 * Switch the focused sample and let the store's reload run to completion.
 *
 * Waits on the rows rather than on `pending`: the flag is still false between
 * the write and the dependency watcher running, so waiting for it to be false
 * can be satisfied before the reload has even started.
 */
const switchTo = async (sampleItemId, peakIds) => {
  get.mockImplementation((url) =>
    url.startsWith('/samples/') ? Promise.resolve(peaksOf(...peakIds)) : Promise.resolve([])
  )
  sample.focusedId = sampleItemId
  await vi.waitFor(() => expect(store.list.map((peak) => peak.peak_id)).toEqual(peakIds), WAIT)
  await nextTick()
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  features.on = true
  sample = parents.sample = reactive({ focusedId: null })
  batch = parents.batch = reactive({ focusedId: 'b-1' })
  ledger = parents.ledger = reactive({
    pending: false,
    error: null,
    list: [{ batch_peak_id: 'bp-1' }]
  })
  localStorage.clear()
})

const load = async () => {
  const { usePeak } = await import('@/stores/data/modules/peak')
  store = usePeak()
}

/** Every counterpart lookup the store issued. */
const counterpartCalls = () =>
  get.mock.calls.filter(([url]) => url === '/batch-peaks/records/counterpart')

describe('peak store: focus follows the sample switch', () => {
  it('asks for the counterpart of the focused peak and focuses it', async () => {
    await load()
    await switchTo('s-1', ['p-a'])
    store.focus({ peak_id: 'p-a' })

    get.mockImplementation((url) =>
      url === '/batch-peaks/records/counterpart'
        ? Promise.resolve([{ sample_item_id: 's-2', sample_peak_id: 'p-b' }])
        : Promise.resolve(peaksOf('p-b'))
    )
    sample.focusedId = 's-2'
    await nextTick()
    await vi.waitFor(() => expect(store.focusedId).toBe('p-b'), WAIT)

    const [, config] = counterpartCalls()[0]
    expect(config.params).toEqual({
      sample_item_id: 's-1',
      sample_peak_id: 'p-a',
      target_sample_item_id: 's-2'
    })
    // Silent by construction: a failure here must not reach the user.
    expect(config.errors).toBe('inline')
    // The whole record, not just the id -- the inspector and the spectrum read
    // `mz` off it.
    expect(store.focused).toMatchObject({ peak_id: 'p-b' })
  })

  it('anchors on the sample the peak belongs to, not the one being left', async () => {
    await load()
    await switchTo('s-1', ['p-a'])
    store.focus({ peak_id: 'p-a' })

    // A burst: s-2 is opened and left again before its peaks have landed, so
    // the focused peak still belongs to s-1 when the second switch fires. The
    // hanging fetch is what holds it there, so waiting for each lookup does not
    // let the store settle in between.
    get.mockImplementation(() => new Promise(() => {}))
    sample.focusedId = 's-2'
    await vi.waitFor(() => expect(counterpartCalls()).toHaveLength(1), WAIT)
    sample.focusedId = 's-3'
    await vi.waitFor(() => expect(counterpartCalls()).toHaveLength(2), WAIT)

    const targets = counterpartCalls().map(([, config]) => config.params)
    expect(targets).toEqual([
      { sample_item_id: 's-1', sample_peak_id: 'p-a', target_sample_item_id: 's-2' },
      { sample_item_id: 's-1', sample_peak_id: 'p-a', target_sample_item_id: 's-3' }
    ])
  })

  it('does not look up anything when no peak is focused', async () => {
    await load()
    await switchTo('s-1', ['p-a'])

    await switchTo('s-2', ['p-b'])

    expect(counterpartCalls()).toHaveLength(0)
  })

  it('forgets the peak the user cleared themselves', async () => {
    await load()
    await switchTo('s-1', ['p-a'])
    store.focus({ peak_id: 'p-a' })
    // Clicking the focused peak again empties the selection; that is a choice,
    // not a vacancy for the next sample switch to fill.
    store.unfocus()

    await switchTo('s-2', ['p-b'])

    expect(counterpartCalls()).toHaveLength(0)
  })

  it('does not follow on the first sample of a session', async () => {
    await load()

    await switchTo('s-1', ['p-a'])

    expect(counterpartCalls()).toHaveLength(0)
  })

  it('does not follow into another batch', async () => {
    await load()
    await switchTo('s-1', ['p-a'])
    store.focus({ peak_id: 'p-a' })

    batch.focusedId = 'b-2'
    await switchTo('s-9', ['p-z'])

    expect(counterpartCalls()).toHaveLength(0)
  })

  it('retires the peak when the user leaves the batch and comes back', async () => {
    await load()
    await switchTo('s-1', ['p-a'])
    store.focus({ peak_id: 'p-a' })

    // Out to another batch. The cascade clears the focus from inside a reload,
    // so nothing else retires the anchor -- only leaving the batch does.
    batch.focusedId = 'b-2'
    await nextTick()
    await switchTo('s-9', ['p-z'])
    await switchTo('s-8', ['p-y'])

    // Back again, with an empty selection the whole time. A peak the user last
    // looked at before the excursion must not reappear now.
    batch.focusedId = 'b-1'
    await nextTick()
    await switchTo('s-1', ['p-a'])
    await switchTo('s-2', ['p-b'])

    expect(counterpartCalls()).toHaveLength(0)
    expect(store.focusedId).toBeNull()
  })

  it('picks the peak back up on returning to the sample it came from', async () => {
    await load()
    await switchTo('s-1', ['p-a'])
    store.focus({ peak_id: 'p-a' })

    // s-2 has no counterpart, so the focus is cleared on the way out.
    get.mockImplementation((url) =>
      url === '/batch-peaks/records/counterpart'
        ? Promise.resolve([])
        : Promise.resolve(peaksOf('p-b'))
    )
    sample.focusedId = 's-2'
    await vi.waitFor(() => expect(counterpartCalls()).toHaveLength(1), WAIT)
    await nextTick()
    expect(store.focusedId).toBeNull()

    // Going back to s-1 must return the user to the peak they were on: it is
    // still in that sample's list, and a same-sample lookup resolves it to its
    // own occurrence.
    get.mockImplementation((url) =>
      url === '/batch-peaks/records/counterpart'
        ? Promise.resolve([{ sample_item_id: 's-1', sample_peak_id: 'p-a' }])
        : Promise.resolve(peaksOf('p-a'))
    )
    sample.focusedId = 's-1'
    await vi.waitFor(() => expect(store.focusedId).toBe('p-a'), WAIT)
  })

  it('forgets a peak the user clears while the store is reloading in place', async () => {
    await load()
    await switchTo('s-1', ['p-a'])
    store.focus({ peak_id: 'p-a' })

    // A reload of the SAME sample -- a retry or a socket refresh -- leaves the
    // ledger clickable, so `pending` alone cannot tell this clear from the one
    // a sample switch's reload performs.
    let releasePeaks
    get.mockImplementation(
      (url) =>
        new Promise((resolve) => {
          if (url.startsWith('/samples/')) releasePeaks = () => resolve(peaksOf('p-a'))
        })
    )
    store.load('retry')
    await vi.waitFor(() => expect(store.pending).toBe(true), WAIT)
    store.unfocus()
    releasePeaks()
    await vi.waitFor(() => expect(store.pending).toBe(false), WAIT)

    await switchTo('s-2', ['p-b'])

    expect(counterpartCalls()).toHaveLength(0)
    expect(store.focusedId).toBeNull()
  })

  it('does not look up anything when the batch has no batch peaks', async () => {
    await load()
    await switchTo('s-1', ['p-a'])
    store.focus({ peak_id: 'p-a' })

    ledger.list = []
    await switchTo('s-2', ['p-b'])

    expect(counterpartCalls()).toHaveLength(0)
    expect(store.focusedId).toBeNull()
  })

  it('still asks when the batch-peak ledger failed to load', async () => {
    await load()
    await switchTo('s-1', ['p-a'])
    store.focus({ peak_id: 'p-a' })

    // An empty list because the load failed says nothing about whether batch
    // peaks exist, so the backend answers rather than the feature going quiet.
    ledger.list = []
    ledger.error = new Error('nope')
    get.mockImplementation((url) =>
      url === '/batch-peaks/records/counterpart'
        ? Promise.resolve([])
        : Promise.resolve(peaksOf('p-b'))
    )
    sample.focusedId = 's-2'
    await nextTick()
    await vi.waitFor(() => expect(counterpartCalls()).toHaveLength(1), WAIT)
  })

  it('leaves the selection empty when the peak has no counterpart', async () => {
    await load()
    await switchTo('s-1', ['p-a'])
    store.focus({ peak_id: 'p-a' })

    get.mockImplementation((url) =>
      url === '/batch-peaks/records/counterpart'
        ? Promise.resolve([])
        : Promise.resolve(peaksOf('p-b'))
    )
    sample.focusedId = 's-2'
    await nextTick()
    await vi.waitFor(() => expect(counterpartCalls()).toHaveLength(1), WAIT)
    await nextTick()

    expect(store.focusedId).toBeNull()
  })

  it('registers no follow at all when peak assignment is off', async () => {
    features.on = false
    await load()
    await switchTo('s-1', ['p-a'])
    store.focus({ peak_id: 'p-a' })

    await switchTo('s-2', ['p-b'])

    expect(counterpartCalls()).toHaveLength(0)
    expect(store.focusedId).toBeNull()
  })
})
