import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// verify() posts a verdict and then refetches, and its caller (PanePeakAssign)
// treats a resolved verify() as "saved and visible" - it closes the form on the
// strength of that. Since sync() records a failed refetch instead of rejecting,
// verify() has to re-raise it, and has to do so from ITS OWN load rather than
// from the store-wide `error` ref that any concurrent sync overwrites.

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

vi.mock('@/stores/data/modules/sample', () => ({
  useSample: () => ({ focusedId: 'si-1' })
}))

vi.mock('@/stores/auth', () => ({ useAuth: () => ({ user: {}, onLogin: vi.fn() }) }))

// Stands in for the ledger store, holding whatever `ledger` currently maps. The
// family resolution itself is the REAL one (imported past the mock): stubbing
// m0Of as well would let this store and the rule it reads verdicts through
// drift apart without a test noticing.
vi.mock('@/stores/data/modules/peakAssignment/assignment', async () => {
  const actual = await vi.importActual('@/stores/data/modules/peakAssignment/assignment')
  return {
    ...actual,
    usePeakAssignment: () => ({
      byId: ledger,
      m0Of: (row) => actual.familyM0(row, ledger)
    })
  }
})

// An assignment and its M+1 satellite, shaped as the engine writes them: a
// satellite carries its M0's formula and mechanism verbatim, so `sample_peak_id`
// is the only one of the three identity fields that differs across a family.
// That single divergence is the whole bug - two thirds of a satellite's identity
// already matches, and it still misses.
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

const VERDICT = {
  assignment_verification_id: 'v1',
  sample_peak_id: 'p1',
  assigned_formula: 'C6H12O6',
  ionization_mechanism_id: 'm1',
  verdict: 'confirmed'
}

let ledger
let usePeakAssignmentVerification

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  ledger = new Map([
    [M0.peak_assignment_id, M0],
    [CHILD.peak_assignment_id, CHILD]
  ])
  ;({ usePeakAssignmentVerification } =
    await import('@/stores/data/modules/peakAssignment/verification'))
})

describe('peakAssignment verification store', () => {
  it('resolves once the verdict is saved and the refetch has landed', async () => {
    post.mockResolvedValue({ data: [VERDICT] })
    get.mockResolvedValue([VERDICT])

    const store = usePeakAssignmentVerification()
    const saved = await store.verify({ peak_assignment_id: 'a1', verdict: 'confirmed' })

    expect(saved).toEqual(VERDICT)
    expect(store.forAssignment(VERDICT)).toEqual(VERDICT)
  })

  // The badge is what the user reads the verdict off. If the refetch failed, the
  // badge still shows the previous verdict, so resolving here would close the
  // form over a value that never changed.
  it('re-raises a refetch that failed, so the form is not closed over a stale badge', async () => {
    post.mockResolvedValue({ data: [VERDICT] })
    const refusal = new Error('503 Service Unavailable')
    get.mockRejectedValue(refusal)

    const store = usePeakAssignmentVerification()

    await expect(store.verify({ peak_assignment_id: 'a1', verdict: 'confirmed' })).rejects.toBe(
      refusal
    )
  })

  // The store-wide `error` ref belongs to whichever sync wrote it last. Reading
  // it here would report an unrelated reload's failure as this verdict failing
  // to save - the form would stay open over a verdict that did save.
  it('does not adopt a failure that belongs to another sync', async () => {
    post.mockResolvedValue({ data: [VERDICT] })
    get.mockResolvedValue([VERDICT])

    const store = usePeakAssignmentVerification()

    // A background reload fails and leaves its error on the store...
    get.mockRejectedValueOnce(new Error('unrelated reload failed'))
    await store.load('socket event')
    expect(store.error).toBeInstanceOf(Error)

    // ...but this verify()'s own refetch succeeds, so it must resolve.
    await expect(store.verify({ peak_assignment_id: 'a1', verdict: 'confirmed' })).resolves.toEqual(
      VERDICT
    )
  })
})

// One verdict covers the isotopologue family: an M+1 is the same compound as its
// M0 seen through one heavy atom, so it is judged with it and never apart from
// it. Verdict records only ever carry an M0's sample_peak_id, which is what lets
// the family rule work with no schema change - and what keeps the calibration
// fed one label per family instead of N correlated ones.
describe('peakAssignment verification family scope', () => {
  const posted = () => post.mock.calls[0][1]

  beforeEach(() => {
    post.mockResolvedValue({ data: [VERDICT] })
    get.mockResolvedValue([VERDICT])
  })

  it('shows the compound its verdict when a satellite is the row in hand', async () => {
    const store = usePeakAssignmentVerification()
    await store.load('verification')

    expect(store.forAssignment(M0)).toEqual(VERDICT)
    expect(store.forAssignment(CHILD)).toEqual(VERDICT)
  })

  it('reports no verdict for a family that has none', async () => {
    get.mockResolvedValue([])
    const store = usePeakAssignmentVerification()
    await store.load('verification')

    expect(store.forAssignment(CHILD)).toBeNull()
    expect(store.forAssignment(null)).toBeNull()
  })

  // Resolution is strict rather than a preference order: a satellite reads its
  // M0's verdict even when an older build left one on the satellite itself.
  // Preferring the satellite's own record is how a family would come to disagree
  // with itself row by row, which is the thing this WP exists to stop.
  it('ignores a verdict an older build left on the satellite itself', async () => {
    const onChild = { ...VERDICT, assignment_verification_id: 'v-old', sample_peak_id: 'p2' }
    get.mockResolvedValue([onChild])
    const store = usePeakAssignmentVerification()
    await store.load('verification')

    expect(store.forAssignment(CHILD)).toBeNull()
  })

  it('records a verdict captured from a satellite against the compound', async () => {
    const store = usePeakAssignmentVerification()

    await store.verify({ peak_assignment_id: CHILD.peak_assignment_id, verdict: 'rejected' })

    expect(post).toHaveBeenCalledTimes(1)
    expect(posted().peak_assignment_id).toBe(M0.peak_assignment_id)
    // Only the target is rewritten; the judgment itself travels untouched.
    expect(posted().verdict).toBe('rejected')
  })

  it('leaves a verdict captured on the compound alone', async () => {
    const store = usePeakAssignmentVerification()

    await store.verify({
      peak_assignment_id: M0.peak_assignment_id,
      verdict: 'confirmed',
      evidence_level: 'msms'
    })

    expect(posted()).toEqual({
      peak_assignment_id: M0.peak_assignment_id,
      verdict: 'confirmed',
      evidence_level: 'msms'
    })
  })

  // The redirect is the store's rule, not the form's, so that curation tools
  // added later cannot post a family a second label by going straight to it.
  it('redirects whichever caller posts the satellite', async () => {
    const store = usePeakAssignmentVerification()

    await store.verify({ peak_assignment_id: CHILD.peak_assignment_id, verdict: 'unsure' })

    expect(posted().peak_assignment_id).toBe(M0.peak_assignment_id)
  })

  // An id the loaded run does not know is not ours to reinterpret - the server
  // decides what it means (and refuses it if it means nothing).
  it('passes an id the run does not know straight through', async () => {
    const store = usePeakAssignmentVerification()

    await store.verify({ peak_assignment_id: 'pa-elsewhere', verdict: 'confirmed' })

    expect(posted().peak_assignment_id).toBe('pa-elsewhere')
  })
})
