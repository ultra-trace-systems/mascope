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
  // load() early-returns without a collection id; the getter cases never reach it.
  useMatchCollection: () => ({ focused: { target_collection_id: 'COLLECTION' } }),
  useMatchIon: () => ({ list: [] })
}))
vi.mock('@/stores/data/modules/match/params', () => ({
  useMatchParams: () => ({ ui: {}, db: {}, set: vi.fn() })
}))

const { api } = await import('@/api')
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

// The colour of an isotope is not the store's to invent: the spectra chart picks
// it when it draws the trace and writes it back onto the row. A reload refetches
// the rows, so the store carries the colour over from the rows it is replacing -
// otherwise the swatch in the rating dialog goes blank on every reload and only
// comes back once the socket has re-streamed the spectra.
describe('match.visualized isotope colours', () => {
  /** The rgb() string the spectra chart writes back onto a drawn row. */
  const TRACE_COLOR = 'rgb(31, 119, 180)'

  /**
   * Answer the next load with one ion and its isotopes. The ion carries the ids
   * `load` resolves the following reload from, so reloading needs no arguments.
   */
  const serve = (ionId, isotopes) => {
    api.http.post.mockImplementation(async (url) => {
      if (!url.endsWith('/ion')) return {}
      return {
        match_ions: [{ target_ion_id: ionId, match: { sample_item_id: 'SAMPLE' } }],
        match_isotopes: isotopes
      }
    })
  }

  /** The colour of each row, in the order the store holds them. */
  const colors = () => store.isotopes.map((row) => row.color)

  beforeEach(() => {
    // The ids of the first load, which has no previous response to resolve from.
    store.ion = { target_ion_id: 'nitrate', match: { sample_item_id: 'SAMPLE' } }
  })

  it('carries a drawn colour across a reload of the same ion', async () => {
    serve('nitrate', NITRATE)
    await store.reload()
    expect(colors()).toEqual([null, null])

    // The spectra chart draws one of the two lines and colours its row.
    store.isotopes[0].color = TRACE_COLOR

    await store.reload()

    // The drawn row keeps its colour; the undrawn one has none to keep.
    expect(colors()).toEqual([TRACE_COLOR, null])
  })

  it('starts a different ion colourless', async () => {
    serve('nitrate', NITRATE)
    await store.reload()
    store.isotopes[0].color = TRACE_COLOR

    serve('bromine', BROMINE)
    await store.reload()

    expect(colors()).toEqual([null, null])
  })

  // The early return for an ion the response carries no match for: it leaves an
  // empty list, so the next load has no colour to carry over either.
  it('keeps nothing when the ion comes back unmatched', async () => {
    serve('nitrate', NITRATE)
    await store.reload()
    store.isotopes[0].color = TRACE_COLOR

    api.http.post.mockImplementation(async () => ({ match_ions: [], match_isotopes: [] }))
    await store.reload()
    expect(store.isotopes).toEqual([])

    serve('nitrate', NITRATE)
    await store.reload()
    expect(colors()).toEqual([null, null])
  })
})
