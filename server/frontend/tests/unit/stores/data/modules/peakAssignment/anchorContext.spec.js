import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const get = vi.fn()
vi.mock('@/api', () => ({
  api: {
    http: { get: (...args) => get(...args), post: vi.fn() },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))
vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: true }))
vi.mock('@/stores/data/modules/sample', () => ({
  useSample: () => ({ focusedId: 'si-1' })
}))
vi.mock('@/stores/auth', () => ({ useAuth: () => ({ user: {}, onLogin: vi.fn() }) }))
vi.mock('@/stores/data/modules/peakAssignment/assignment', () => ({
  usePeakAssignment: () => ({
    // Stands in for the store's family resolution: a child answers with its M0.
    m0Of: (row) =>
      row?.role === 'iso_child' ? (ledger.get(row.owner_peak_assignment_id) ?? row) : row
  })
}))

const M0 = {
  peak_assignment_id: 'pa-m0',
  sample_peak_id: 'p1',
  assigned_formula: 'C6H12O6',
  ionization_mechanism_id: 'm1',
  role: 'M0'
}
const CHILD = {
  peak_assignment_id: 'pa-c1',
  sample_peak_id: 'p2',
  assigned_formula: 'C6H12O6',
  ionization_mechanism_id: 'm1',
  role: 'iso_child',
  owner_peak_assignment_id: 'pa-m0'
}
// The same peak, assigned to another formula: a dissenting sample.
const DISSENT = { ...M0, peak_assignment_id: 'pa-d', assigned_formula: 'C7H14O7' }
// What the anchor-context route serves: the live verdict on the anchor p1 folded
// into, with the peak id on it.
const REACHING = {
  batch_peak_verification_id: 'bv1',
  sample_peak_id: 'p1',
  batch_peak_id: 'bp-1',
  assigned_formula: 'C6H12O6',
  ionization_mechanism_id: 'm1',
  verdict: 'confirmed',
  superseded_utc: null,
  stale: false
}

let ledger
let usePeakAssignmentAnchorContext
beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  ledger = new Map([
    [M0.peak_assignment_id, M0],
    [CHILD.peak_assignment_id, CHILD]
  ])
  ;({ usePeakAssignmentAnchorContext } =
    await import('@/stores/data/modules/peakAssignment/anchorContext'))
})

async function loaded(records) {
  get.mockResolvedValue(records)
  const store = usePeakAssignmentAnchorContext()
  await store.load('test')
  return store
}

describe('peakAssignment anchorContext store', () => {
  it("fetches the focused sample's anchor context", async () => {
    await loaded([REACHING])
    expect(get).toHaveBeenCalledWith(
      '/batch-peaks/sample/si-1/anchor-context',
      expect.objectContaining({ use: 'read' })
    )
  })

  it('overlays an assignment whose peak folded into the judged anchor with the same claim', async () => {
    const store = await loaded([REACHING])
    expect(store.overlayFor(M0)).toEqual(REACHING)
  })

  it('resolves an isotopologue through its M0', async () => {
    const store = await loaded([REACHING])
    expect(store.overlayFor(CHILD)).toEqual(REACHING)
  })

  it('gives a dissenting row no overlay from a verdict about another formula', async () => {
    const store = await loaded([REACHING])
    expect(store.overlayFor(DISSENT)).toBeNull()
  })

  it('gives a row with no formula nothing', async () => {
    const store = await loaded([REACHING])
    expect(store.overlayFor({ ...M0, assigned_formula: null })).toBeNull()
    expect(store.overlayFor(null)).toBeNull()
  })

  it('compares mechanisms null-safely', async () => {
    const store = await loaded([{ ...REACHING, ionization_mechanism_id: null }])
    expect(store.overlayFor({ ...M0, ionization_mechanism_id: undefined })).toEqual({
      ...REACHING,
      ionization_mechanism_id: null
    })
    expect(store.overlayFor(M0)).toBeNull()
  })
})
