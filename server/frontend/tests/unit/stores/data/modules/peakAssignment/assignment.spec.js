import { describe, it, expect, vi } from 'vitest'

// `familyM0` is the one rule that decides which row a family-scoped judgment is
// about, and three surfaces lean on it (the verification store's read and write,
// the inspector's verify form, the ledger's verdict column). It is pure, so it
// is pinned here directly rather than through any of them.

vi.mock('@/api', () => ({
  api: {
    http: { get: vi.fn(), post: vi.fn() },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))

vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: true }))

vi.mock('@/stores/data/modules/sample', () => ({ useSample: () => ({ focusedId: 'si-1' }) }))

vi.mock('@/stores/auth', () => ({ useAuth: () => ({ user: {}, onLogin: vi.fn() }) }))

const { familyM0 } = await import('@/stores/data/modules/peakAssignment/assignment')

const M0 = {
  peak_assignment_id: 'pa-m0',
  sample_peak_id: 'p-1',
  assigned_formula: 'C6H12O6',
  role: 'M0'
}
// An isotopologue carries its M0's formula verbatim (the engine copies it), so the
// role and the owner id are the only things that mark it as one.
const CHILD = {
  peak_assignment_id: 'pa-c1',
  sample_peak_id: 'p-2',
  assigned_formula: 'C6H12O6',
  role: 'iso_child',
  owner_peak_assignment_id: 'pa-m0'
}
const UNASSIGNED = { peak_assignment_id: 'pa-u', sample_peak_id: 'p-3', role: 'unassigned' }

const ledger = new Map([
  [M0.peak_assignment_id, M0],
  [CHILD.peak_assignment_id, CHILD],
  [UNASSIGNED.peak_assignment_id, UNASSIGNED]
])

describe('familyM0', () => {
  it('resolves an isotopologue to the M0 that names it', () => {
    expect(familyM0(CHILD, ledger)).toBe(M0)
  })

  it('leaves every other row as its own anchor', () => {
    expect(familyM0(M0, ledger)).toBe(M0)
    expect(familyM0(UNASSIGNED, ledger)).toBe(UNASSIGNED)
    // Reagent and artifact rows are roles orthogonal to the isotopologue tree;
    // they own no family and belong to none.
    const reagent = { peak_assignment_id: 'pa-r', role: 'reagent' }
    expect(familyM0(reagent, ledger)).toBe(reagent)
  })

  it('has no answer for no row', () => {
    expect(familyM0(null, ledger)).toBeNull()
    expect(familyM0(undefined, ledger)).toBeNull()
  })

  // An orphan should not happen - a run stores the owner alongside its children.
  // Returning null would make the callers treat a real row as no row at all: the
  // ledger would blank its verdict column and the inspector would refuse to
  // verify a peak that plainly carries a formula.
  it('falls back to the orphan itself when its owner is not loaded', () => {
    const orphan = { ...CHILD, owner_peak_assignment_id: 'pa-gone' }
    expect(familyM0(orphan, ledger)).toBe(orphan)
    expect(familyM0(orphan, new Map())).toBe(orphan)
    expect(familyM0(orphan, null)).toBe(orphan)
  })

  // The owner id arrives off the wire and is compared by Map lookup, which does
  // not coerce: a number owner id would silently miss a string-keyed ledger and
  // orphan every isotopologue in the run. Pinned so the day it changes type, this
  // fails rather than the badges quietly going blank.
  it('matches the owner id exactly, without coercion', () => {
    const numeric = { ...CHILD, owner_peak_assignment_id: 7 }
    expect(familyM0(numeric, new Map([['7', M0]]))).toBe(numeric)
    expect(familyM0(numeric, new Map([[7, M0]]))).toBe(M0)
  })
})
