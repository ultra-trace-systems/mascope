import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

let app
vi.mock('@/stores', () => ({ useApp: () => app }))

const { HISTORY_KEY, useBatchLedgerReturn } =
  await import('@/lib/panes/PaneBrowserMatch/stores/batchLedgerReturn.js')

const SAMPLES = [{ sample_item_id: 's-1' }, { sample_item_id: 's-2' }]

function makeApp({ sample = null, batchPeaks = [{ batch_peak_id: 'bp-1' }], mode } = {}) {
  const state = reactive({
    data: {
      batch: { focusedId: 'b-1' },
      batchPeak: { list: batchPeaks },
      sample: {
        list: SAMPLES,
        focusedId: sample,
        focus: vi.fn((s) => {
          state.data.sample.focusedId = s.sample_item_id
        }),
        unfocus: vi.fn(() => {
          state.data.sample.focusedId = null
        })
      }
    },
    ui: { matchMode: { mode: mode ?? 'assignments' } }
  })
  return state
}

// vue-router's own fields, as it keeps them in every entry it writes.
const ROUTER_STATE = { back: null, current: '/', forward: '/', position: 3, replaced: true }

const entry = () => window.history.state?.[HISTORY_KEY] ?? null
/** The browser's Back or Forward landing on an entry with this state. */
const popTo = (state) => window.dispatchEvent(new PopStateEvent('popstate', { state }))

let store
let pushState
let back

beforeEach(() => {
  window.history.replaceState({ ...ROUTER_STATE }, '')
  pushState = vi.spyOn(window.history, 'pushState')
  // jsdom's traversal is a queued task; the tests land it themselves (popTo).
  back = vi.spyOn(window.history, 'back').mockImplementation(() => {})
})

afterEach(() => {
  store?.$dispose()
  store = null
  vi.restoreAllMocks()
})

function make(options) {
  app = makeApp(options)
  setActivePinia(createPinia())
  store = useBatchLedgerReturn()
  return store
}

describe('batchLedgerReturn: when the way back is offered', () => {
  it('is offered on a sample of a batch that has batch peaks', () => {
    expect(make({ sample: 's-1' }).available).toBe(true)
  })

  it('is not offered before the batch has a ledger', () => {
    expect(make({ sample: 's-1', batchPeaks: [] }).available).toBe(false)
  })

  it('is not offered without a sample or without a batch', () => {
    expect(make().available).toBe(false)
    make({ sample: 's-1' })
    app.data.batch.focusedId = null
    expect(store.available).toBe(false)
  })
})

describe('batchLedgerReturn: from a batch ledger row and back', () => {
  it('marks the entry it leaves and pushes the sample, beside vue-router', () => {
    make()
    store.leaveFromRow('bp-1', 's-1')

    expect(pushState).toHaveBeenCalledTimes(1)
    const [pushed] = pushState.mock.calls[0]
    expect(pushed[HISTORY_KEY]).toEqual({
      level: 'sample',
      batchId: 'b-1',
      sampleId: 's-1',
      batchPeakId: 'bp-1',
      overBatch: true
    })
    // One position on from vue-router's entry, with nothing ahead of it.
    expect(pushed).toMatchObject({ position: 4, back: '/', current: '/', forward: null })
    expect(store.origin).toEqual({
      sample_batch_id: 'b-1',
      batch_peak_id: 'bp-1',
      sample_item_id: 's-1'
    })
  })

  it("leaves the entry it came from as the batch ledger's, naming the row", () => {
    make()
    const replace = vi.spyOn(window.history, 'replaceState')
    store.leaveFromRow('bp-1', 's-1')
    const [left] = replace.mock.calls[0]
    expect(left).toMatchObject({ ...ROUTER_STATE })
    expect(left[HISTORY_KEY]).toEqual({ level: 'batch', batchId: 'b-1', batchPeakId: 'bp-1' })
  })

  it("the control is the browser's own Back after a jump", () => {
    make()
    store.leaveFromRow('bp-1', 's-1')
    app.data.sample.focusedId = 's-1'

    store.back()
    expect(back).toHaveBeenCalledTimes(1)
    // Nothing more until the browser lands on the entry behind.
    expect(app.data.sample.unfocus).not.toHaveBeenCalled()

    popTo({
      ...ROUTER_STATE,
      [HISTORY_KEY]: { level: 'batch', batchId: 'b-1', batchPeakId: 'bp-1' }
    })
    expect(app.data.sample.unfocus).toHaveBeenCalledTimes(1)
    expect(store.reveal).toBe('bp-1')
  })

  it('Forward opens the sample again, from the same row', () => {
    make()
    store.origin = null
    popTo({
      ...ROUTER_STATE,
      [HISTORY_KEY]: { level: 'sample', batchId: 'b-1', sampleId: 's-2', batchPeakId: 'bp-1' }
    })
    expect(app.data.sample.focus).toHaveBeenCalledWith(SAMPLES[1])
    expect(store.origin).toEqual({
      sample_batch_id: 'b-1',
      batch_peak_id: 'bp-1',
      sample_item_id: 's-2'
    })
  })

  it('a step through the members keeps the row, on the same entry', () => {
    make()
    store.leaveFromRow('bp-1', 's-1')
    store.stepped('s-2')
    app.data.sample.focusedId = 's-2'

    expect(entry()).toMatchObject({ level: 'sample', sampleId: 's-2', batchPeakId: 'bp-1' })
    expect(pushState).toHaveBeenCalledTimes(1)
    store.back()
    expect(back).toHaveBeenCalledTimes(1)
  })
})

describe('batchLedgerReturn: reached another way', () => {
  it('pushes the batch ledger with no row, so Back returns to the sample', () => {
    make({ sample: 's-2' })
    store.back()

    expect(back).not.toHaveBeenCalled()
    expect(app.data.sample.unfocus).toHaveBeenCalledTimes(1)
    expect(store.reveal).toBeNull()
    expect(entry()).toEqual({ level: 'batch', batchId: 'b-1', batchPeakId: null })
    const [pushed] = pushState.mock.calls[0]
    expect(pushed).toMatchObject({ position: 4 })

    // The entry behind names the sample, so Back opens it again.
    popTo({ ...ROUTER_STATE, [HISTORY_KEY]: { level: 'sample', batchId: 'b-1', sampleId: 's-2' } })
    expect(app.data.sample.focus).toHaveBeenCalledWith(SAMPLES[1])
  })

  it('brings no row back once the reader moved to a sample the row did not open', () => {
    make()
    store.leaveFromRow('bp-1', 's-1')
    // The sample list, not a step: the history entry still names s-1.
    app.data.sample.focusedId = 's-2'

    store.back()
    expect(back).not.toHaveBeenCalled()
    expect(store.reveal).toBeNull()
    expect(entry()).toEqual({ level: 'batch', batchId: 'b-1', batchPeakId: null })
  })

  it('brings the row back when the reader returns to its sample by hand', () => {
    make({ sample: 's-1' })
    store.origin = { sample_batch_id: 'b-1', batch_peak_id: 'bp-1', sample_item_id: 's-1' }
    store.back()
    expect(store.reveal).toBe('bp-1')
    expect(entry()).toEqual({ level: 'batch', batchId: 'b-1', batchPeakId: 'bp-1' })
  })
})

describe('batchLedgerReturn: never Back out of the app', () => {
  /** The browser's Back or Forward: the entry becomes current, then pops. */
  const landOn = (state) => {
    window.history.replaceState(state, '')
    popTo(state)
  }

  it('the sample entry its own fallback wrote is no way back by Back', () => {
    // A sample opened from the sidebar or a shared link.
    make({ sample: 's-1' })
    const replace = vi.spyOn(window.history, 'replaceState')
    store.back()
    // Back onto the entry the fallback left behind the batch entry it pushed.
    const [behind] = replace.mock.calls[0]
    landOn(behind)
    expect(app.data.sample.focusedId).toBe('s-1')

    store.back()
    expect(back).not.toHaveBeenCalled()
    expect(app.data.sample.unfocus).toHaveBeenCalledTimes(2)
    expect(entry()).toEqual({ level: 'batch', batchId: 'b-1', batchPeakId: null })
  })

  it('a row jump, Back, another sample from the list, the control, Back: still no Back', () => {
    make()
    store.leaveFromRow('bp-1', 's-1')
    app.data.sample.focusedId = 's-1'
    landOn({
      ...ROUTER_STATE,
      [HISTORY_KEY]: { level: 'batch', batchId: 'b-1', batchPeakId: 'bp-1' }
    })
    expect(app.data.sample.focusedId).toBeNull()

    // The sample list: no entry of its own.
    app.data.sample.focusedId = 's-2'
    store.back()
    expect(back).not.toHaveBeenCalled()
    landOn({
      ...ROUTER_STATE,
      [HISTORY_KEY]: { level: 'sample', batchId: 'b-1', sampleId: 's-2', batchPeakId: null }
    })
    expect(app.data.sample.focusedId).toBe('s-2')

    store.back()
    expect(back).not.toHaveBeenCalled()
    expect(entry()).toEqual({ level: 'batch', batchId: 'b-1', batchPeakId: null })
  })
})

describe('batchLedgerReturn: entries that name nothing on screen', () => {
  const batchEntry = { level: 'batch', batchId: 'b-1', batchPeakId: 'bp-1' }

  it("ignores another batch's entry", () => {
    make({ sample: 's-1' })
    popTo({ ...ROUTER_STATE, [HISTORY_KEY]: { ...batchEntry, batchId: 'b-2' } })
    expect(app.data.sample.unfocus).not.toHaveBeenCalled()
  })

  it('ignores an entry while the browser shows targets', () => {
    make({ sample: 's-1', mode: 'targets' })
    popTo({ ...ROUTER_STATE, [HISTORY_KEY]: batchEntry })
    expect(app.data.sample.unfocus).not.toHaveBeenCalled()
  })

  it("ignores an entry that is not this store's", () => {
    make({ sample: 's-1' })
    popTo({ ...ROUTER_STATE })
    popTo(null)
    expect(app.data.sample.unfocus).not.toHaveBeenCalled()
  })

  it('does not unfocus again on the batch ledger, nor focus a sample it lacks', () => {
    make()
    popTo({ ...ROUTER_STATE, [HISTORY_KEY]: batchEntry })
    expect(app.data.sample.unfocus).not.toHaveBeenCalled()
    popTo({ ...ROUTER_STATE, [HISTORY_KEY]: { level: 'sample', batchId: 'b-1', sampleId: 's-9' } })
    expect(app.data.sample.focus).not.toHaveBeenCalled()
  })

  it('stops listening once disposed', () => {
    make({ sample: 's-1' })
    store.$dispose()
    store = null
    popTo({ ...ROUTER_STATE, [HISTORY_KEY]: batchEntry })
    expect(app.data.sample.unfocus).not.toHaveBeenCalled()
  })
})

describe('batchLedgerReturn: guards', () => {
  it('writes nothing for a jump without a sample or a batch', () => {
    make()
    store.leaveFromRow('bp-1', null)
    app.data.batch.focusedId = null
    store.leaveFromRow('bp-1', 's-1')
    expect(pushState).not.toHaveBeenCalled()
    expect(store.origin).toBeNull()
  })

  it('a step with no row behind it is no step back to one', () => {
    make()
    window.history.replaceState(
      { ...ROUTER_STATE, [HISTORY_KEY]: { level: 'sample', batchId: 'b-1', sampleId: 's-1' } },
      ''
    )
    store.stepped('s-2')
    expect(store.origin).toBeNull()
    expect(entry().sampleId).toBe('s-1')
  })

  it('goes nowhere without a sample open', () => {
    make()
    store.back()
    expect(pushState).not.toHaveBeenCalled()
    expect(back).not.toHaveBeenCalled()
    expect(app.data.sample.unfocus).not.toHaveBeenCalled()
  })

  it("brings back no row of another batch's", () => {
    make({ sample: 's-1' })
    store.origin = { sample_batch_id: 'b-2', batch_peak_id: 'bp-1', sample_item_id: 's-1' }
    store.back()
    expect(store.reveal).toBeNull()
  })

  it('names the sample on the entry it leaves, so Back can open it again', () => {
    make({ sample: 's-2' })
    const replace = vi.spyOn(window.history, 'replaceState')
    store.back()
    const [left] = replace.mock.calls[0]
    expect(left).toMatchObject({ ...ROUTER_STATE })
    expect(left[HISTORY_KEY]).toEqual({
      level: 'sample',
      batchId: 'b-1',
      sampleId: 's-2',
      batchPeakId: null
    })
  })
})
