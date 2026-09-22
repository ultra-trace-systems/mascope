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

describe('acquisition store: the look-back of a status filter', () => {
  it('goes by when the status was recorded while a status is chosen', async () => {
    const store = useAcquisition()

    store.processingStatus = ['failed']
    await nextTick()
    expect(lastRequest()[1].params.recent_by).toBe('processing')

    store.processingStatus = null
    await nextTick()
    expect(lastRequest()[1].params.recent_by).toBeUndefined()
  })

  it('sends one request when Clear filters changes the time and the status', async () => {
    const store = useAcquisition()
    store.processingStatus = ['failed']
    store.time.mode = 'Last 7 days'
    await nextTick()
    api.http.get.mockClear()

    store.resetFilters()
    await nextTick()

    expect(api.http.get).toHaveBeenCalledTimes(1)
  })
})

const handler = (event) => api.socket.on.mock.calls.find(([name]) => name === event)[1]
const row = (id, status) => ({
  sample_file_id: id,
  instrument: 'Orbi-1',
  processing_status: status
})
const answer = (...rows) => ({ data: { data: rows, results: rows.length } })

describe('acquisition store: loading and live updates', () => {
  it('keeps the answer of the latest request when an earlier one lands last', async () => {
    const store = useAcquisition()
    let answerEarlier
    api.http.get
      .mockImplementationOnce(() => new Promise((resolve) => (answerEarlier = resolve)))
      .mockResolvedValueOnce(answer(row('sf-new', 'done')))

    const earlier = store.load()
    await store.load()
    answerEarlier(answer(row('sf-old', 'done')))
    await earlier

    expect(store.list.map((f) => f.sample_file_id)).toEqual(['sf-new'])
  })

  it('applies an update that arrived during a load on top of its answer', async () => {
    const store = useAcquisition()
    let answerLoad
    api.http.get.mockImplementationOnce(() => new Promise((resolve) => (answerLoad = resolve)))

    const loading = store.load()
    handler('acquisition_updated')({
      record_id: 'sf-1',
      record: row('sf-1', 'needs_chemistry')
    })
    answerLoad(answer(row('sf-1', 'converted')))
    await loading

    expect(store.list[0].processing_status).toBe('needs_chemistry')
  })

  it('reloads when an update moves a listed file out of the status filter', async () => {
    vi.useFakeTimers()
    try {
      const store = useAcquisition()
      store.processingStatus = ['converted', 'queued', 'bound', 'calibrated']
      await nextTick()
      store.list = [row('sf-1', 'bound')]
      api.http.get.mockClear()

      handler('acquisition_updated')({ record_id: 'sf-1', record: row('sf-1', 'done') })
      handler('acquisition_updated')({ record_id: 'sf-1', record: row('sf-1', 'done') })

      expect(store.list[0].processing_status).toBe('bound')
      vi.advanceTimersByTime(300)
      expect(api.http.get).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('reloads when an update brings a file into the status filter', async () => {
    vi.useFakeTimers()
    try {
      const store = useAcquisition()
      store.processingStatus = ['failed']
      await nextTick()
      api.http.get.mockClear()

      handler('acquisition_updated')({ record_id: 'sf-2', record: row('sf-2', 'failed') })
      vi.advanceTimersByTime(300)

      expect(api.http.get).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('leaves the list alone for a file it does not show when no status is chosen', async () => {
    vi.useFakeTimers()
    try {
      useAcquisition()
      api.http.get.mockClear()

      handler('acquisition_updated')({ record_id: 'sf-3', record: row('sf-3', 'failed') })
      vi.advanceTimersByTime(300)

      expect(api.http.get).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })
})
