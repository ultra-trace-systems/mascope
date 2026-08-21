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

const VERDICT = {
  assignment_verification_id: 'v1',
  sample_peak_id: 'p1',
  assigned_formula: 'C6H12O6',
  ionization_mechanism_id: 'm1',
  verdict: 'confirmed'
}

let usePeakAssignmentVerification

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
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
