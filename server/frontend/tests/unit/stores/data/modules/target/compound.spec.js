import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// Find structure asks the structures route for one page of the reference rows
// of a formula. A blank CAS number or name is not sent, since the route would
// rank on it, and a failure is the dialog's to show, not a toast.

vi.mock('@/api', () => ({
  api: {
    http: { get: vi.fn(), patch: vi.fn() },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))
vi.mock('@/lib/store', () => ({ useData: () => ({ list: [] }) }))

const { api } = await import('@/api')
const { useTargetCompound } = await import('@/stores/data/modules/target/compound')

const ANSWER = { message: '', results: 0, formula: 'CH4', data: [] }

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  api.http.get.mockResolvedValue({ status: 200, data: ANSWER })
})

describe('target compound store: findStructures', () => {
  it('reads one page of the structures route, inline', async () => {
    const found = await useTargetCompound().findStructures({
      formula: 'CH4',
      cas_number: ' 74-82-8 ',
      name: 'Methane',
      page: 2,
      limit: 10
    })

    expect(found).toEqual(ANSWER)
    expect(api.http.get).toHaveBeenCalledWith('/target/compounds/structures', {
      params: { formula: 'CH4', cas_number: '74-82-8', name: 'Methane', page: 2, limit: 10 },
      type: 'find_target_compound_structures',
      errors: 'inline'
    })
  })

  it('sends no blank CAS number or name', async () => {
    await useTargetCompound().findStructures({
      formula: 'CH4',
      cas_number: '  ',
      name: null,
      limit: 10
    })

    const [, { params }] = api.http.get.mock.calls[0]
    expect(params).toEqual({
      formula: 'CH4',
      cas_number: undefined,
      name: undefined,
      page: 0,
      limit: 10
    })
  })
})
