import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// Removing an ion's instrument params reloads the visualization. Both stores are
// real here: the point is how the params store sequences that reload, which a
// mocked visualization store could not get wrong.

vi.mock('@/api', () => ({
  api: {
    http: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))

vi.mock('@/stores/ui', () => ({ useUi: () => ({ chart: { clear: vi.fn() } }) }))
vi.mock('@/stores/auth', () => ({ useAuth: () => ({ onLogin: vi.fn() }) }))
vi.mock('@/stores/data/modules/dataset', () => ({ useDataset: () => ({ focused: null }) }))
vi.mock('@/stores/data/modules/sample', () => ({ useSample: () => ({ focused: null }) }))
vi.mock('@/stores/data/modules/match', () => ({
  useMatchCollection: () => ({ focused: { target_collection_id: 'COLLECTION' } }),
  useMatchIon: () => ({ list: [] })
}))

const { api } = await import('@/api')
const { useMatchParams } = await import('@/stores/data/modules/match/params')
const { useMatchVisualized } = await import('@/stores/data/modules/match/visualized')

const isotope = (mz, id) => ({ target_isotope_id: id, mz })
const NITRATE = [isotope(250.0932, 'nitrate-m0'), isotope(251.0903, 'nitrate-15n')]

/** The rgb() string the spectra chart writes back onto a drawn row. */
const TRACE_COLOR = 'rgb(31, 119, 180)'

/** The aggregate calls only - the visualization store also POSTs ion_focus. */
const aggregateCalls = () =>
  api.http.post.mock.calls.filter(([url]) => url.endsWith('/ion')).length

let params
let visualized

beforeEach(() => {
  setActivePinia(createPinia())
  params = useMatchParams()
  visualized = useMatchVisualized()

  api.http.patch.mockResolvedValue({})
  api.http.post.mockImplementation(async (url) => {
    if (!url.endsWith('/ion')) return {}
    return {
      match_ions: [
        { target_ion_id: 'nitrate', match: { sample_item_id: 'SAMPLE' }, filter_params: {} }
      ],
      match_isotopes: NITRATE
    }
  })

  visualized.ion = {
    target_ion_id: 'nitrate',
    match: { sample_item_id: 'SAMPLE' },
    filter_params: {}
  }
})

describe('match.params remove', () => {
  beforeEach(async () => {
    // The params the tab opens with, then a first load and the colour the
    // spectra chart writes onto a drawn row.
    params.set()
    await visualized.reload()
    visualized.isotopes[0].color = TRACE_COLOR
    api.http.post.mockClear()
  })

  it('reloads the visualization once', async () => {
    await params.remove()

    // Two overlapping loads would each refetch and restream the spectra, and the
    // later one would be the one whose rows land.
    expect(aggregateCalls()).toBe(1)
  })

  it('leaves the drawn colour on the isotope', async () => {
    await params.remove()

    expect(visualized.isotopes.map((row) => row.color)).toEqual([TRACE_COLOR, null])
  })
})
