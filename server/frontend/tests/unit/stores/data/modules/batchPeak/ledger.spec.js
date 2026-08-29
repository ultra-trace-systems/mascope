import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// The tier strip above the batch-peak ledger is a breakdown of the table below
// it, so what it counts has to be what the table shows at top level. A batch
// peak that is an isotopologue satellite carries its family's formula and its
// family's tier - counting it counts one species twice, and leaves the strip
// claiming more rows in a tier than folding the ledger can produce.

const get = vi.fn()

vi.mock('@/api', () => ({
  api: {
    http: { get: (...args) => get(...args), post: vi.fn() },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))

vi.mock('@/stores/data/modules/batch', () => ({ useBatch: () => ({ focusedId: 'sb-1' }) }))

let useBatchPeakLedger

const peak = (batch_peak_id, consensus_tier, satellite_of = null) => ({
  batch_peak_id,
  consensus_tier,
  satellite_of,
  mz: 181.0707
})

/** The store with `peaks` loaded, the way a ledger response delivers them. */
const loaded = async (peaks) => {
  get.mockResolvedValue(peaks)
  const ledger = useBatchPeakLedger()
  await ledger.load('test')
  return ledger
}

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  ;({ useBatchPeakLedger } = await import('@/stores/data/modules/batchPeak/ledger'))
})

describe('batch-peak ledger tier counts', () => {
  it('counts one bucket per tier, always all four', async () => {
    const ledger = await loaded([peak('bp-1', 'assigned'), peak('bp-2', 'candidate')])

    expect(ledger.tierCounts).toEqual({
      assigned: 1,
      candidate: 1,
      below_assignability: 0,
      unassigned: 0
    })
  })

  it('leaves isotopologue satellites out, so the counts are of species', async () => {
    const ledger = await loaded([
      peak('bp-m0', 'assigned'),
      peak('bp-sat-1', 'assigned', 'bp-m0'),
      peak('bp-sat-2', 'assigned', 'bp-m0'),
      peak('bp-other', 'candidate')
    ])

    // One assigned species, not three: the two satellites are the same compound
    // measured at another isotope, and the ledger folds them under it.
    expect(ledger.tierCounts.assigned).toBe(1)
    expect(ledger.tierCounts.candidate).toBe(1)
    // The list itself keeps every anchor - only the histogram is of species.
    expect(ledger.list).toHaveLength(4)
  })

  it('counts a row with an unreadable tier as unassigned rather than dropping it', async () => {
    const ledger = await loaded([peak('bp-1', null), peak('bp-2', 'nonsense')])

    expect(ledger.tierCounts.unassigned).toBe(2)
  })
})
