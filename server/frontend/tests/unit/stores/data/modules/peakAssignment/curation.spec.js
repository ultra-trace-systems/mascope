import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// The manual-curation write on the assignment store: it commits a different
// composition for one peak and then reloads the run, because an override also
// rewrites rows the caller never named (the satellites of the formula it
// replaced) and every surface reads the one list.
//
// The rule this file exists to pin is the one that differs from its neighbour:
// a verdict is redirected to the family M0, and an override is NOT. An
// alternative is identified by its position in ONE row's candidate list, so
// re-pointing the write at a different row would commit whatever happens to sit
// at that index there.

const patch = vi.fn()
const get = vi.fn()

vi.mock('@/api', () => ({
  api: {
    http: {
      patch: (...args) => patch(...args),
      get: (...args) => get(...args),
      post: vi.fn()
    },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))

vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: true }))

vi.mock('@/stores/data/modules/sample', () => ({
  useSample: () => ({ focusedId: SAMPLE_ID })
}))

vi.mock('@/stores/data/modules/peakAssignment/run', () => ({
  usePeakAssignmentRun: () => ({
    focused: {
      peak_assignment_run_id: 'run-1',
      sample_item_id: 'si-1',
      status: 'completed'
    }
  })
}))

vi.mock('@/stores/auth', () => ({ useAuth: () => ({ user: {}, onLogin: vi.fn() }) }))

let SAMPLE_ID = 'si-1'

// An M0 and its satellite, as the engine writes them. The satellite carries the
// M0's formula verbatim; only the peak differs.
const M0 = {
  peak_assignment_id: 'pa-m0',
  sample_peak_id: 'p1',
  sample_peak_mz: 181.07,
  assigned_formula: 'C6H12O6',
  ionization_mechanism_id: 'm1',
  role: 'M0',
  tier: 'assigned',
  source: 'database'
}
const CHILD = {
  peak_assignment_id: 'pa-c1',
  sample_peak_id: 'p2',
  sample_peak_mz: 182.07,
  assigned_formula: 'C6H12O6',
  ionization_mechanism_id: 'm1',
  role: 'iso_child',
  tier: 'assigned',
  source: 'database',
  owner_peak_assignment_id: 'pa-m0'
}

// What the run looks like after the override landed: the M0 carries the
// promoted formula and is marked manual, and its satellite has been demoted.
const CURATED = {
  ...M0,
  assigned_formula: 'C7H16O5',
  source: 'manual',
  tier: 'candidate'
}
const DEMOTED = {
  ...CHILD,
  assigned_formula: null,
  role: 'unassigned',
  tier: 'unassigned',
  source: 'manual',
  owner_peak_assignment_id: null
}

const PROMOTE = {
  action: 'promote_alternative',
  alternative_index: 0,
  expected_formula: 'C7H16O5'
}

let usePeakAssignment

/** The ledger the paged loader will hand back on the next reload. */
const servePage = (rows) => get.mockResolvedValue({ data: { data: rows, total: rows.length } })

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  SAMPLE_ID = 'si-1'
  servePage([M0, CHILD])
  ;({ usePeakAssignment } = await import('@/stores/data/modules/peakAssignment/assignment'))
})

describe('peakAssignment curate()', () => {
  const patched = () => patch.mock.calls[0]

  it('patches the named assignment with the action verbatim', async () => {
    const store = usePeakAssignment()
    servePage([CURATED, DEMOTED])

    await store.curate('pa-m0', PROMOTE)

    expect(patch).toHaveBeenCalledTimes(1)
    const [url, body] = patched()
    expect(url).toBe('/peak-assignments/sample/si-1/assignment/pa-m0')
    expect(body).toEqual(PROMOTE)
  })

  // The one rule that differs from a verdict's. `verify()` rewrites the target
  // to the family M0 on the caller's behalf; an override must not, or a "use
  // this" on a satellite's third alternative would commit the M0's third.
  it('writes against the row it was given, not the family M0', async () => {
    const store = usePeakAssignment()
    servePage([CURATED, DEMOTED])

    await store.curate('pa-c1', { action: 'promote_alternative', alternative_index: 2 })

    expect(patched()[0]).toBe('/peak-assignments/sample/si-1/assignment/pa-c1')
  })

  it('reloads the run and answers with the curated row', async () => {
    const store = usePeakAssignment()
    await store.load('initial')
    expect(store.forPeak('p1').assigned_formula).toBe('C6H12O6')

    servePage([CURATED, DEMOTED])
    const row = await store.curate('pa-m0', PROMOTE)

    expect(row.assigned_formula).toBe('C7H16O5')
    expect(row.source).toBe('manual')
    // The satellite the override displaced comes back demoted, which is why the
    // action reloads instead of patching the one row it named.
    expect(store.forPeak('p2').tier).toBe('unassigned')
  })

  // Same reasoning as verify(): the caller treats a resolved curate() as "saved
  // and visible" and stops showing a spinner on the strength of it, so a refetch
  // that failed has to reach it - sync() records failures rather than rejecting.
  it('re-raises a refetch that failed', async () => {
    const store = usePeakAssignment()
    const refusal = new Error('503 Service Unavailable')
    get.mockRejectedValue(refusal)

    await expect(store.curate('pa-m0', PROMOTE)).rejects.toBe(refusal)
  })

  it('lets the write itself reject, so a refused override is not reported as saved', async () => {
    const store = usePeakAssignment()
    const forbidden = Object.assign(new Error('Forbidden'), { response: { status: 403 } })
    patch.mockRejectedValue(forbidden)

    await expect(store.curate('pa-m0', PROMOTE)).rejects.toBe(forbidden)
    expect(get).not.toHaveBeenCalled()
  })

  it('does nothing without a focused sample or an assignment to curate', async () => {
    const store = usePeakAssignment()

    expect(await store.curate(null, PROMOTE)).toBeNull()
    SAMPLE_ID = null
    expect(await store.curate('pa-m0', PROMOTE)).toBeNull()
    expect(patch).not.toHaveBeenCalled()
  })
})
