import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { SPECIES_PAGE_SIZE } from '@/lib/referenceSources'

// The reference store reads the deployment's loaded lists through the shared
// data loader, keyed by source name, and reads one source's species a page at
// a time for the Reference lists dialog. Both reads leave a failure to the
// dialog, which shows it in place.

vi.mock('@/api', () => ({ api: { http: { get: vi.fn() } } }))

// The fetcher and options the store gives useData are captured, so the read
// it describes can be run here.
const captured = vi.hoisted(() => ({ name: null, fetcher: null, options: null }))
vi.mock('@/lib/store', () => ({
  useData: (name, fetcher, options) => {
    Object.assign(captured, { name, fetcher, options })
    return {}
  }
}))

import { api } from '@/api'
import { useReference } from '@/stores/data/modules/reference'

beforeEach(() => {
  setActivePinia(createPinia())
  api.http.get.mockReset()
})

describe('reference store', () => {
  it('lists the sources from the sources route, keyed by name, failures shown inline', async () => {
    useReference()
    expect(captured.name).toBe('reference')
    expect(captured.options).toEqual({ key: 'name' })

    api.http.get.mockResolvedValue([{ name: 'a' }])
    expect(await captured.fetcher()).toEqual([{ name: 'a' }])
    expect(api.http.get).toHaveBeenCalledWith('/reference/sources', {
      use: 'read',
      type: 'load_reference_sources',
      errors: 'inline'
    })
  })

  it("reads a page of one version's species, and its total", async () => {
    api.http.get.mockResolvedValue({ data: { data: [{ formula: 'C10H16O7' }], total: 573 } })
    const answer = await useReference().species('monoterpene-hom-kang2021', {
      version: '2026.09',
      page: 2
    })

    expect(answer).toEqual({ data: [{ formula: 'C10H16O7' }], total: 573 })
    expect(api.http.get).toHaveBeenCalledWith(
      '/reference/sources/monoterpene-hom-kang2021/compounds',
      {
        params: { version: '2026.09', page: 2, limit: SPECIES_PAGE_SIZE },
        type: 'load_reference_species',
        errors: 'inline'
      }
    )
  })

  it('reads the active version, page 0, when none is named, and escapes the name', async () => {
    api.http.get.mockResolvedValue({ data: {} })
    const answer = await useReference().species('my list/v')

    expect(answer).toEqual({ data: [], total: 0 })
    const [url, { params }] = api.http.get.mock.calls[0]
    expect(url).toBe('/reference/sources/my%20list%2Fv/compounds')
    expect(params).toEqual({ page: 0, limit: SPECIES_PAGE_SIZE })
  })

  it('leaves a failed species read to the caller', async () => {
    api.http.get.mockRejectedValue(new Error('down'))
    await expect(useReference().species('a')).rejects.toThrow('down')
  })
})
