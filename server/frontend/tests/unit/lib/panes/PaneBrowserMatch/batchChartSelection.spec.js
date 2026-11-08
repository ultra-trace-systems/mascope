import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { MAX_SELECTED_BATCH_PEAKS } from '@/lib/batchChart'

let app
vi.mock('@/stores', () => ({ useApp: () => app }))

const { useBatchChartSelection } =
  await import('@/lib/panes/PaneBrowserMatch/stores/batchChartSelection.js')

/** A batch ledger store stand-in with the selection helper's shape. */
function ledger(ids, selectedIds = [], instrumentTypeOf = {}) {
  const list = ids.map((batch_peak_id) => ({
    batch_peak_id,
    instrument_type: instrumentTypeOf[batch_peak_id]
  }))
  const store = {
    list,
    // The instrument partition shown: null on a batch of one instrument type.
    instrumentType: Object.keys(instrumentTypeOf).length ? 'orbi' : null,
    inPartition: (bp) =>
      store.instrumentType == null || bp.instrument_type === store.instrumentType,
    showInstrument: vi.fn((type) => {
      store.instrumentType = type
      const before = store.selected.length
      store.selected = store.selected.filter(store.inPartition)
      return before - store.selected.length
    }),
    selected: selectedIds.map((id) => list.find((r) => r.batch_peak_id === id)),
    isSelected: (arg) => store.selected.some((r) => r.batch_peak_id === arg.batch_peak_id),
    select: vi.fn((arg) => {
      const record = list.find((r) => r.batch_peak_id === arg.batch_peak_id)
      if (record) store.selected = [...store.selected, record]
    }),
    unselect: vi.fn((arg) => {
      store.selected = store.selected.filter((r) => r.batch_peak_id !== arg.batch_peak_id)
    })
  }
  return store
}

function makeApp(batchPeak) {
  return { data: { batchPeak }, ui: { notification: { push: vi.fn() } } }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('useBatchChartSelection', () => {
  it('plots a row species by selecting its batch peak, and takes it out again', () => {
    app = makeApp(ledger(['bp-1', 'bp-2']))
    const chart = useBatchChartSelection()
    const row = { peak_assignment_id: 'pa-1', batch_peak_id: 'bp-1' }

    expect(chart.canPlot(row)).toBe(true)
    expect(chart.isPlotted(row)).toBe(false)
    expect(chart.toggle(row)).toBe(true)
    expect(app.data.batchPeak.select).toHaveBeenCalledWith({ batch_peak_id: 'bp-1' })
    expect(chart.isPlotted(row)).toBe(true)

    expect(chart.toggle(row)).toBe(true)
    expect(app.data.batchPeak.unselect).toHaveBeenCalledWith({ batch_peak_id: 'bp-1' })
    expect(chart.isPlotted(row)).toBe(false)
  })

  it('cannot plot a row whose peak is not in the batch ledger', () => {
    app = makeApp(ledger(['bp-1']))
    const chart = useBatchChartSelection()
    const row = { peak_assignment_id: 'pa-9', batch_peak_id: null }
    expect(chart.canPlot(row)).toBe(false)
    expect(chart.isPlotted(row)).toBe(false)
    expect(chart.toggle(row)).toBe(false)
    expect(app.data.batchPeak.select).not.toHaveBeenCalled()
  })

  it('says when the ledger has not loaded the species yet', () => {
    app = makeApp(ledger(['bp-1']))
    const chart = useBatchChartSelection()
    expect(chart.toggle({ batch_peak_id: 'bp-7' })).toBe(false)
    expect(app.ui.notification.push).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'warning', message: expect.stringContaining('not loaded') })
    )
  })

  it("switches the chart to a species' instrument type, and says so", () => {
    app = makeApp(ledger(['bp-1', 'bp-2'], ['bp-1'], { 'bp-1': 'orbi', 'bp-2': 'tof' }))
    const chart = useBatchChartSelection()
    expect(chart.toggle({ batch_peak_id: 'bp-2' })).toBe(true)
    const store = app.data.batchPeak
    expect(store.showInstrument).toHaveBeenCalledWith('tof')
    expect(store.selected.map((r) => r.batch_peak_id)).toEqual(['bp-2'])
    expect(app.ui.notification.push).toHaveBeenCalledWith(
      expect.objectContaining({
        status: 'info',
        message: expect.stringMatching(/draws the batch's TOF species; its Orbitrap ones/)
      })
    )
  })

  it('says only that the ledger switched when the selection held nothing of the other type', () => {
    app = makeApp(ledger(['bp-1', 'bp-2'], [], { 'bp-1': 'orbi', 'bp-2': 'tof' }))
    const chart = useBatchChartSelection()
    expect(chart.toggle({ batch_peak_id: 'bp-2' })).toBe(true)
    expect(app.data.batchPeak.showInstrument).toHaveBeenCalledWith('tof')
    const { message } = app.ui.notification.push.mock.calls[0][0]
    expect(message).toBe("The Batch peaks ledger now shows the batch's TOF species.")
  })

  it('plots a species of the shown instrument type without a word', () => {
    app = makeApp(ledger(['bp-1', 'bp-2'], ['bp-1'], { 'bp-1': 'orbi', 'bp-2': 'orbi' }))
    const chart = useBatchChartSelection()
    expect(chart.toggle({ batch_peak_id: 'bp-2' })).toBe(true)
    expect(app.data.batchPeak.showInstrument).not.toHaveBeenCalled()
    expect(app.ui.notification.push).not.toHaveBeenCalled()
  })

  it('holds the chart cap and says so', () => {
    const ids = Array.from({ length: MAX_SELECTED_BATCH_PEAKS + 1 }, (_, i) => `bp-${i}`)
    app = makeApp(ledger(ids, ids.slice(0, MAX_SELECTED_BATCH_PEAKS)))
    const chart = useBatchChartSelection()
    expect(chart.toggle({ batch_peak_id: `bp-${MAX_SELECTED_BATCH_PEAKS}` })).toBe(false)
    expect(app.data.batchPeak.select).not.toHaveBeenCalled()
    expect(app.ui.notification.push).toHaveBeenCalledWith(
      expect.objectContaining({ message: expect.stringContaining('full') })
    )
    // Taking one out is always allowed.
    expect(chart.toggle({ batch_peak_id: 'bp-0' })).toBe(true)
  })
})
