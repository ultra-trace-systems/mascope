import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import axios from 'axios'

// The Raw files list filters by processing status on the server, so the
// filter spans every page rather than narrowing the one on screen. FastAPI
// reads a list from a repeated key (`processing_status=a&processing_status=b`),
// not from axios's default `processing_status[]=a`.

vi.mock('@/api', () => ({
  api: {
    http: { get: vi.fn() },
    socket: { on: vi.fn(), off: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))

vi.mock('@/lib/runtime', () => ({ runtime: { config: {} } }))

vi.mock('@/stores/data/modules/instrument', () => ({
  useInstrument: () => ({ focused: { instrument: 'Orbi-1' } })
}))

let api
let useAcquisition

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  ;({ api } = await import('@/api'))
  api.http.get.mockResolvedValue({ data: { data: [], results: 0 } })
  ;({ useAcquisition } = await import('@/stores/data/modules/acquisition'))
})

const lastRequest = () => api.http.get.mock.calls.at(-1)

describe('acquisition store: processing status filter', () => {
  it('asks the server for the chosen statuses, from the first page', async () => {
    const store = useAcquisition()
    store.first = 200

    store.processingStatus = ['needs_chemistry', 'failed']
    await nextTick()

    const [url, config] = lastRequest()
    expect(url).toBe('/sample/files/recent')
    expect(config.params.processing_status).toEqual(['needs_chemistry', 'failed'])
    expect(config.params.page).toBe(0)
    expect(store.first).toBe(0)
  })

  it('sends a list the way the API reads one', async () => {
    const store = useAcquisition()

    store.processingStatus = ['needs_chemistry', 'failed']
    await nextTick()

    const [url, config] = lastRequest()
    const uri = axios.getUri({
      url,
      params: config.params,
      paramsSerializer: config.paramsSerializer
    })
    expect(uri).toContain('processing_status=needs_chemistry&processing_status=failed')
  })

  it('filters a date range too', async () => {
    const store = useAcquisition()
    store.processingStatus = ['done']
    await nextTick()

    store.time.range.min = new Date('2026-09-01T00:00:00Z')
    await nextTick()

    const [url, config] = lastRequest()
    expect(url).toBe('/sample/files')
    expect(config.params.processing_status).toEqual(['done'])
  })

  it('sends no status at all when any status will do', async () => {
    const store = useAcquisition()
    store.processingStatus = ['done']
    await nextTick()

    store.processingStatus = null
    await nextTick()

    const [, config] = lastRequest()
    expect(config.params.processing_status).toBeUndefined()
  })

  it('is cleared with the other filters', async () => {
    const store = useAcquisition()
    store.processingStatus = ['failed']
    await nextTick()

    store.resetFilters()

    expect(store.processingStatus).toBe(null)
  })
})
