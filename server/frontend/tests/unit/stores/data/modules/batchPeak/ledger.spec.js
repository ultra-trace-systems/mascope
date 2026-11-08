import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const get = vi.fn()
vi.mock('@/api', () => ({
  api: {
    http: { get: (...args) => get(...args), post: vi.fn() },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))
vi.mock('@/stores/data/modules/batch', () => ({
  useBatch: () => ({ focusedId: 'sb-1' })
}))
// The run the ledger is reading, as the run store reports it: null is the
// live ledger, an id is an earlier run's snapshot.
const runState = { viewingId: null }
vi.mock('@/stores/data/modules/batchPeak/run', () => ({
  useBatchPeakRun: () => runState
}))
vi.mock('@/stores/auth', () => ({ useAuth: () => ({ user: {}, onLogin: vi.fn() }) }))

let useBatchPeakLedger
beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  runState.viewingId = null
  get.mockResolvedValue([])
  ;({ useBatchPeakLedger } = await import('@/stores/data/modules/batchPeak/ledger'))
})

describe('batchPeak ledger store and the run it reads', () => {
  it('reads the live ledger with no run named', async () => {
    const store = useBatchPeakLedger()
    await store.load('test')
    expect(get).toHaveBeenCalledWith(
      '/batch-peaks/batch/sb-1',
      expect.objectContaining({ params: { min_n_present: 1 } })
    )
  })

  it("names the run on the read when an earlier run's snapshot is on screen", async () => {
    const store = useBatchPeakLedger()
    runState.viewingId = 'r-old'
    await store.load('switch')
    expect(get).toHaveBeenLastCalledWith(
      '/batch-peaks/batch/sb-1',
      expect.objectContaining({ params: { min_n_present: 1, batch_peak_run_id: 'r-old' } })
    )
  })
})

describe('batchPeak ledger store: the instrument partition', () => {
  const row = (batch_peak_id, instrument_type) => ({ batch_peak_id, instrument_type })

  it('has no partition to choose on a batch of one instrument type', async () => {
    get.mockResolvedValue([row('bp-1', 'tof'), row('bp-2', 'tof')])
    const store = useBatchPeakLedger()
    await store.load('test')
    expect(store.instrumentTypes).toEqual(['tof'])
    expect(store.instrumentType).toBeNull()
    expect(store.list.every(store.inPartition)).toBe(true)
  })

  it('shows Orbitrap first on a batch of both, and keeps the selection to the shown type', async () => {
    get.mockResolvedValue([row('bp-t', 'tof'), row('bp-o', 'orbi'), row('bp-o2', 'orbi')])
    const store = useBatchPeakLedger()
    await store.load('test')
    expect(store.instrumentTypes).toEqual(['orbi', 'tof'])
    expect(store.instrumentType).toBe('orbi')
    expect(store.list.filter(store.inPartition).map((bp) => bp.batch_peak_id)).toEqual([
      'bp-o',
      'bp-o2'
    ])

    store.select({ batch_peak_id: 'bp-o' })
    expect(store.showInstrument('tof')).toBe(1)
    expect(store.instrumentType).toBe('tof')
    expect(store.selectedIds).toEqual([])
    store.select({ batch_peak_id: 'bp-t' })
    expect(store.selectedIds).toEqual(['bp-t'])
    // Nothing of the other type left to drop.
    expect(store.showInstrument('tof')).toBe(0)
  })

  it('keeps showing the selected type when a batch of one type gains the other', async () => {
    get.mockResolvedValue([row('bp-t', 'tof'), row('bp-t2', 'tof')])
    const store = useBatchPeakLedger()
    await store.load('test')
    store.select({ batch_peak_id: 'bp-t' })
    expect(store.instrumentType).toBeNull()

    // An Orbitrap sample folds in, and the ledger reloads with both types.
    get.mockResolvedValue([row('bp-t', 'tof'), row('bp-t2', 'tof'), row('bp-o', 'orbi')])
    await store.load('test')
    expect(store.instrumentTypes).toEqual(['orbi', 'tof'])
    expect(store.selectedIds).toEqual(['bp-t'])
    expect(store.instrumentType).toBe('tof')
    expect(store.selected.every(store.inPartition)).toBe(true)

    // The type was taken from the selection once: unticking its last row, or
    // clearing the selection, keeps the ledger on it.
    store.unselect({ batch_peak_id: 'bp-t' })
    expect(store.selectedIds).toEqual([])
    expect(store.instrumentType).toBe('tof')
    store.select({ batch_peak_id: 'bp-t2' })
    store.unfocus()
    expect(store.instrumentType).toBe('tof')
  })

  it('shows Orbitrap first when a batch gains a second type with nothing selected', async () => {
    get.mockResolvedValue([row('bp-t', 'tof')])
    const store = useBatchPeakLedger()
    await store.load('test')
    // A row ticked and unticked while the batch held one type leaves no trace.
    store.select({ batch_peak_id: 'bp-t' })
    get.mockResolvedValue([row('bp-t', 'tof'), row('bp-t2', 'tof')])
    await store.load('test')
    store.unselect({ batch_peak_id: 'bp-t' })
    get.mockResolvedValue([row('bp-t', 'tof'), row('bp-t2', 'tof'), row('bp-o', 'orbi')])
    await store.load('test')
    expect(store.instrumentType).toBe('orbi')
    // A row ticked later does not move the ledger off the type shown.
    store.select({ batch_peak_id: 'bp-t' })
    expect(store.instrumentType).toBe('orbi')
    // Nor does a reload of a batch that already held both.
    get.mockResolvedValue([
      row('bp-t', 'tof'),
      row('bp-t2', 'tof'),
      row('bp-o', 'orbi'),
      row('bp-o2', 'orbi')
    ])
    await store.load('test')
    expect(store.instrumentType).toBe('orbi')
  })

  it("keeps the user's choice over the selection when the second type returns", async () => {
    get.mockResolvedValue([row('bp-t', 'tof'), row('bp-o', 'orbi')])
    const store = useBatchPeakLedger()
    await store.load('test')
    store.showInstrument('orbi')
    get.mockResolvedValue([row('bp-t', 'tof')])
    await store.load('test')
    store.select({ batch_peak_id: 'bp-t' })
    get.mockResolvedValue([row('bp-t', 'tof'), row('bp-o', 'orbi')])
    await store.load('test')
    expect(store.instrumentType).toBe('orbi')
  })
})
