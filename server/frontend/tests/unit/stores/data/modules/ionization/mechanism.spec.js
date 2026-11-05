import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// The mechanism editor takes a name beside the notation string. The pane
// hands both to the store's create, and only what the store posts reaches the
// server, so the body it sends is what is pinned here.

vi.mock('@/api', () => ({
  api: {
    http: { get: vi.fn(), post: vi.fn(), delete: vi.fn() }
  }
}))

vi.mock('@/lib/store', () => ({
  useData: () => ({})
}))

let api
let useIonizationMechanism

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  ;({ api } = await import('@/api'))
  ;({ useIonizationMechanism } = await import('@/stores/data/modules/ionization/mechanism'))
})

describe('ionization mechanism store: create', () => {
  it('posts the name with the mechanism', () => {
    useIonizationMechanism().create({ ionization_mechanism: '[M+H-H2O]+', name: 'Dehydrated' })

    expect(api.http.post).toHaveBeenCalledTimes(1)
    const [url, body] = api.http.post.mock.calls[0]
    expect(url).toBe('/ionization_mechanisms')
    expect(body).toEqual({ ionization_mechanism: '[M+H-H2O]+', name: 'Dehydrated' })
  })

  it('posts no name when none is given', () => {
    useIonizationMechanism().create({ ionization_mechanism: '[M+H]+' })

    const [, body] = api.http.post.mock.calls[0]
    expect(body).toEqual({ ionization_mechanism: '[M+H]+', name: null })
  })
})
