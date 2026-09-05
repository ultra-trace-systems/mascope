import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const post = vi.fn()
const get = vi.fn()
vi.mock('@/api', () => ({
  api: {
    http: {
      post: (...args) => post(...args),
      get: (...args) => get(...args)
    },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))
vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: true }))
vi.mock('@/stores/data/modules/batch', () => ({
  useBatch: () => ({ focusedId: 'sb-1' })
}))
vi.mock('@/stores/auth', () => ({ useAuth: () => ({ user: {}, onLogin: vi.fn() }) }))
// A write changes what the focused sample shows, so the overlay reloads too.
const contextLoad = vi.fn()
vi.mock('@/stores/data/modules/peakAssignment/anchorContext', () => ({
  usePeakAssignmentAnchorContext: () => ({ load: contextLoad })
}))

/** A ledger row: the claim a batch peak makes now. */
const ROW = { batch_peak_id: 'bp-1', consensus_formula: 'C6H12O6', ionization_mechanism_id: 'm1' }

/** A verdict as the listing serves it, live unless said otherwise. */
const verdict = (id, over = {}) => ({
  batch_peak_verification_id: id,
  batch_peak_id: 'bp-1',
  assigned_formula: 'C6H12O6',
  ionization_mechanism_id: 'm1',
  verdict: 'confirmed',
  superseded_utc: null,
  ...over
})

let useBatchPeakVerification
beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  ;({ useBatchPeakVerification } = await import('@/stores/data/modules/batchPeak/verification'))
})

async function loaded(records) {
  get.mockResolvedValue(records)
  const store = useBatchPeakVerification()
  await store.load('test')
  return store
}

describe('batchPeak verification store', () => {
  it('loads the focused batch verdicts', async () => {
    await loaded([verdict('v1')])
    expect(get).toHaveBeenCalledWith(
      '/batch-peaks/batch/sb-1/verdicts',
      expect.objectContaining({ use: 'read' })
    )
  })

  it("shows the live verdict on the row's present claim", async () => {
    const store = await loaded([
      verdict('v2', { verdict: 'rejected' }),
      verdict('v1', { superseded_utc: '2026-09-04T00:00:00Z' })
    ])
    const shown = store.forAnchor(ROW)
    expect(shown?.batch_peak_verification_id).toBe('v2')
    expect(store.isStale(shown, ROW)).toBe(false)
  })

  it('ignores superseded verdicts', async () => {
    const store = await loaded([verdict('v1', { superseded_utc: '2026-09-04T00:00:00Z' })])
    expect(store.forAnchor(ROW)).toBeNull()
  })

  it('falls back to a live verdict on a claim the row no longer makes, and calls it stale', async () => {
    const store = await loaded([verdict('v1')])
    const moved = { ...ROW, consensus_formula: 'C7H14O7' }
    const shown = store.forAnchor(moved)
    expect(shown?.batch_peak_verification_id).toBe('v1')
    expect(store.isStale(shown, moved)).toBe(true)
  })

  it("prefers the present claim's verdict over a stale one", async () => {
    const store = await loaded([
      verdict('v-new', { assigned_formula: 'C7H14O7', verdict: 'unsure' }),
      verdict('v-old')
    ])
    const moved = { ...ROW, consensus_formula: 'C7H14O7' }
    expect(store.forAnchor(moved)?.batch_peak_verification_id).toBe('v-new')
    expect(store.isStale(store.forAnchor(moved), moved)).toBe(false)
  })

  it('compares mechanisms null-safely', async () => {
    const store = await loaded([verdict('v1', { ionization_mechanism_id: null })])
    const row = { ...ROW, ionization_mechanism_id: undefined }
    expect(store.isStale(store.forAnchor(row), row)).toBe(false)
  })

  it('answers nothing for another anchor, or no row', async () => {
    const store = await loaded([verdict('v1')])
    expect(store.forAnchor({ ...ROW, batch_peak_id: 'bp-9' })).toBeNull()
    expect(store.forAnchor(null)).toBeNull()
  })

  it('posts a verdict to the batch route and reloads, the sample overlay too', async () => {
    post.mockResolvedValue({ data: [verdict('v1')] })
    get.mockResolvedValue([verdict('v1')])
    const store = useBatchPeakVerification()
    const body = {
      batch_peak_id: 'bp-1',
      verdict: 'confirmed',
      evidence_level: 'pattern',
      note: null,
      expected_formula: 'C6H12O6'
    }
    const saved = await store.verify(body)
    expect(post).toHaveBeenCalledWith(
      '/batch-peaks/batch/sb-1/verify',
      body,
      expect.objectContaining({ use: 'create' })
    )
    expect(saved).toEqual(verdict('v1'))
    expect(store.forAnchor(ROW)).toMatchObject(verdict('v1'))
    expect(contextLoad).toHaveBeenCalled()
  })

  it('re-raises a refetch that failed, so the form is not closed over a stale badge', async () => {
    post.mockResolvedValue({ data: [verdict('v1')] })
    const refusal = new Error('503 Service Unavailable')
    get.mockRejectedValue(refusal)
    const store = useBatchPeakVerification()
    await expect(store.verify({ batch_peak_id: 'bp-1', verdict: 'unsure' })).rejects.toBe(refusal)
  })

  it('posts a retract to the batch route and drops the badge', async () => {
    post.mockResolvedValue({ data: [{ batch_peak_verification_id: 'v1' }] })
    get.mockResolvedValue([])
    const store = useBatchPeakVerification()
    await store.retract({ batch_peak_id: 'bp-1' })
    expect(post).toHaveBeenCalledWith(
      '/batch-peaks/batch/sb-1/retract',
      { batch_peak_id: 'bp-1' },
      expect.objectContaining({ use: 'update' })
    )
    expect(store.forAnchor(ROW)).toBeNull()
  })
})
