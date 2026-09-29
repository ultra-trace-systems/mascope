import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// The isotope the match tab opens on. The endpoint returns an ion's isotopes in
// m/z order, so the lightest is first - the M0 of an unlabelled ion, but for an
// ion made with a labelled reagent the reagent's unlabelled remainder, a couple
// of percent of the line the ion is measured by and one mass unit below it.
//
// Derived in the store so the isotope table's marked row and the spectra chart's
// first figure cannot answer it differently; the rule itself is pinned in
// chem.spec.js, and what each view does with the answer in their own specs.

vi.mock('@/api', () => ({
  api: {
    http: { get: vi.fn(), post: vi.fn() },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))

vi.mock('@/stores/ui', () => ({ useUi: () => ({ chart: { clear: vi.fn() } }) }))
vi.mock('@/stores/data/modules/dataset', () => ({ useDataset: () => ({ focused: null }) }))
vi.mock('@/stores/data/modules/sample', () => ({ useSample: () => ({ focused: null }) }))
vi.mock('@/stores/data/modules/match', () => ({
  useMatchCollection: () => ({ focused: {} }),
  useMatchIon: () => ({ list: [] })
}))
vi.mock('@/stores/data/modules/match/params', () => ({
  useMatchParams: () => ({ ui: {}, db: {}, set: vi.fn() })
}))

const { useMatchVisualized } = await import('@/stores/data/modules/match/visualized')

/** An isotope row as the ion aggregate endpoint returns one. */
const isotope = (mz, formula) => ({
  target_isotope_id: formula,
  target_isotope_formula: formula,
  mz
})

const NITRATE = [isotope(250.0932, 'C9H16NO7-'), isotope(251.0903, '[15N]C9H16O7-')]
const BROMINE = [isotope(328.6817, 'CHBr4-'), isotope(332.6776, '[81Br]2CHBr2-')]

let store

/**
 * The formula of the isotope the tab would open on. Pinia hands back a reactive
 * proxy of the row, so the line is named rather than compared by identity.
 */
const mainFormula = () => store.isotopeMain?.target_isotope_formula

beforeEach(() => {
  setActivePinia(createPinia())
  store = useMatchVisualized()
})

describe('match.visualized isotopeMain', () => {
  it("is a labelled ion's labelled line, not the lighter remainder", () => {
    store.ion = { target_ion_formula: 'C9H16O7^N-' }
    store.isotopes = NITRATE

    expect(mainFormula()).toBe('[15N]C9H16O7-')
  })

  it('is the lightest line of an unlabelled bromine cluster, not the tallest', () => {
    store.ion = { target_ion_formula: 'CHBr4-' }
    store.isotopes = BROMINE

    expect(mainFormula()).toBe('CHBr4-')
  })

  it('follows the ion, so switching ions does not leave the old answer', () => {
    store.ion = { target_ion_formula: 'C9H16O7^N-' }
    store.isotopes = NITRATE
    expect(mainFormula()).toBe('[15N]C9H16O7-')

    store.ion = { target_ion_formula: 'CHBr4-' }
    store.isotopes = BROMINE
    expect(mainFormula()).toBe('CHBr4-')
  })

  // The isotopes are null while a load is in flight and [] when the ion matched
  // none; neither is an isotope to open on.
  it('has no answer before the isotopes have loaded, or when there are none', () => {
    store.ion = { target_ion_formula: 'C9H16O7^N-' }
    store.isotopes = null
    expect(store.isotopeMain).toBeUndefined()

    store.isotopes = []
    expect(store.isotopeMain).toBeUndefined()
  })

  // An ion the store has not been told the formula of is read as unlabelled,
  // which for every ion but a labelled one is the same answer.
  it('reads the lightest line when no ion formula is known', () => {
    store.ion = null
    store.isotopes = NITRATE

    expect(mainFormula()).toBe('C9H16NO7-')
  })
})

// The spectra chart draws the M0 and, beside it, the selected isotope when that
// is a different line - `isotopeSelected !== isotopeList[0]`, an identity test
// between two store members. Pinia hands back a reactive proxy rather than the
// row that was assigned, so whether the two agree for one row is a question only
// a real store answers; a component spec's plain-object mock cannot pose it.
describe('match.visualized isotopeMain identity', () => {
  beforeEach(() => {
    store.ion = { target_ion_formula: 'C9H16O7^N-' }
    store.isotopes = NITRATE
  })

  it('is the same object as the selection when the M0 is what is selected', () => {
    store.isotopeSelected = store.isotopes[1]

    expect(store.isotopeSelected === store.isotopeMain).toBe(true)
  })

  it('is a different object from the selection when another line is selected', () => {
    store.isotopeSelected = store.isotopes[0]

    expect(store.isotopeSelected === store.isotopeMain).toBe(false)
  })

  // Stated, not guarded. The reads are stable whether or not the getter
  // memoises: vue caches a reactive proxy by its raw target, so re-running the
  // rule over the same rows hands back the same objects either way. Only a
  // getter that both re-evaluated and copied would fail here, and the copy alone
  // already fails the case above, so nothing reaches this one first.
  it('gives back the same object on every read', () => {
    expect(store.isotopeMain === store.isotopeMain).toBe(true)
  })
})
