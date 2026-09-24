import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick, reactive } from 'vue'
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

// Reactive, because the store watches the focused instrument: the rooms it
// holds and the events it acts on both follow it, and "no instrument" is a
// state of its own rather than the absence of one.
const focus = vi.fn()
const instrumentStore = vi.hoisted(() => ({ state: null }))
vi.mock('@/stores/data/modules/instrument', () => ({
  useInstrument: () => instrumentStore.state
}))

const INSTRUMENTS = [{ instrument: 'Orbi-1' }, { instrument: 'Tof-2' }]

let api
let useAcquisition

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  instrumentStore.state = reactive({
    focused: { instrument: 'Orbi-1' },
    list: [...INSTRUMENTS],
    focus
  })
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

describe('acquisition store: opening the files a notification names', () => {
  it('shows one status of one instrument since a time, with the page filters cleared', async () => {
    const store = useAcquisition()
    store.search = 'blank'
    store.polarity = '+'

    store.showFiles({
      instrument: 'Orbi-1',
      status: 'failed',
      since: new Date('2026-09-20T07:59:00Z')
    })
    await nextTick()

    expect(focus).toHaveBeenCalledWith({ instrument: 'Orbi-1' })
    expect(store.search).toBe('')
    expect(store.polarity).toBe('')
    expect(store.processingStatus).toEqual(['failed'])
    expect(store.time.mode).toBe('range')
    const [url, config] = lastRequest()
    expect(url).toBe('/sample/files')
    expect(config.params.processing_status).toEqual(['failed'])
    expect(config.params.datetime_min).toBe('2026-09-20T07:59:00.000Z')
  })

  it('clears the page filters with the others', () => {
    const store = useAcquisition()
    store.search = 'blank'
    store.polarity = '-'

    store.resetFilters()

    expect(store.search).toBe('')
    expect(store.polarity).toBe('')
  })
})

// Listing every instrument at once is the store's unfocused state, not a
// filter value. Two things follow from it: the request carries no instrument,
// and the socket rooms are every instrument's rather than one - acquisition
// events are emitted into the room named after the file's own instrument, so
// a list spanning them all has to hold them all or it never hears of a new
// file. The rooms are held only while the list is on screen.
describe('acquisition store: all instruments', () => {
  const rooms = () => new Set(api.socket.addSubscription.mock.calls.map(([room]) => room))
  const dropped = () => new Set(api.socket.removeSubscription.mock.calls.map(([room]) => room))
  const handler = (event) => api.socket.on.mock.calls.find(([name]) => name === event)?.[1]

  // The reloads these events cause are debounced, so the clock is ours.
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  // Watching is what the Raw files pane reports; nothing is subscribed until
  // it does, and the initial load is not what these tests are counting.
  const watching = async (store) => {
    store.setWatching(true)
    await nextTick()
    api.http.get.mockClear()
    return store
  }

  // Unfocusing reloads by itself - the list is a different list now. Clearing
  // here keeps that load from standing in for the one a test is looking for.
  const showAll = async () => {
    instrumentStore.state.focused = null
    await nextTick()
    api.http.get.mockClear()
  }

  it('subscribes to nothing until the list is on screen', () => {
    useAcquisition()

    expect(rooms()).toEqual(new Set())
  })

  it('holds only the focused instrument room while one is focused', async () => {
    const store = useAcquisition()
    store.setWatching(true)
    await nextTick()

    expect(rooms()).toEqual(new Set(['Orbi-1']))
  })

  it('holds every instrument room with none focused', async () => {
    await watching(useAcquisition())
    await showAll()

    expect(rooms()).toEqual(new Set(['Orbi-1', 'Tof-2']))
    // Orbi-1 is still listed, so it is not dropped and resubscribed.
    expect(dropped()).toEqual(new Set())
  })

  it('takes a room for an instrument that appears later', async () => {
    await watching(useAcquisition())
    await showAll()

    instrumentStore.state.list = [...INSTRUMENTS, { instrument: 'Api-3' }]
    await nextTick()

    expect(rooms()).toContain('Api-3')
  })

  it('gives the other rooms back when an instrument is focused again', async () => {
    await watching(useAcquisition())
    await showAll()

    instrumentStore.state.focused = { instrument: 'Tof-2' }
    await nextTick()

    expect(dropped()).toEqual(new Set(['Orbi-1']))
  })

  it('gives every room back when the list leaves the screen', async () => {
    const store = await watching(useAcquisition())
    await showAll()

    store.setWatching(false)
    await nextTick()

    expect(dropped()).toEqual(new Set(['Orbi-1', 'Tof-2']))
  })

  it('reloads when the list comes back, having heard nothing while away', async () => {
    const store = await watching(useAcquisition())
    store.setWatching(false)
    await nextTick()
    api.http.get.mockClear()

    store.setWatching(true)
    await nextTick()

    expect(api.http.get).toHaveBeenCalled()
  })

  it('asks the server for no instrument in particular', async () => {
    const store = await watching(useAcquisition())
    await showAll()
    await store.load()

    expect(lastRequest()[1].params.instrument).toBeUndefined()
  })

  it('ignores a file created on an instrument the list is not showing', async () => {
    await watching(useAcquisition())

    handler('acquisition_created')({ record: { instrument: 'Tof-2' } })
    vi.advanceTimersByTime(5000)

    expect(api.http.get).not.toHaveBeenCalled()
  })

  // The server announces a brand new instrument's first file into that
  // instrument's room - before the instrument itself exists, so before anyone
  // could have joined it. The event is lost, and the instrument turning up in
  // the list is the only remaining sign that there is something to fetch.
  it('reloads when an instrument it has never seen appears', async () => {
    await watching(useAcquisition())
    await showAll()

    instrumentStore.state.list = [...INSTRUMENTS, { instrument: 'Api-3' }]
    await nextTick()
    vi.advanceTimersByTime(400)

    expect(api.http.get).toHaveBeenCalled()
  })

  it('leaves a new instrument alone while one is focused', async () => {
    await watching(useAcquisition())

    instrumentStore.state.list = [...INSTRUMENTS, { instrument: 'Api-3' }]
    await nextTick()
    vi.advanceTimersByTime(5000)

    expect(api.http.get).not.toHaveBeenCalled()
  })

  it('reloads once for a burst of files from several instruments', async () => {
    await watching(useAcquisition())
    await showAll()

    for (const instrument of ['Orbi-1', 'Tof-2', 'Orbi-1']) {
      handler('acquisition_created')({ record: { instrument } })
    }
    vi.advanceTimersByTime(400)

    expect(api.http.get).toHaveBeenCalledTimes(1)
  })

  // Several instruments ingesting at once never go quiet for the debounce's
  // wait, and a plain debounce would then hold the reload off for as long as
  // the traffic lasted - leaving the list stale exactly while it is busiest.
  it('still reloads under a stream of events that never pauses', async () => {
    await watching(useAcquisition())
    await showAll()

    for (let elapsed = 0; elapsed < 3000; elapsed += 200) {
      handler('acquisition_created')({ record: { instrument: 'Orbi-1' } })
      vi.advanceTimersByTime(200)
    }

    expect(api.http.get).toHaveBeenCalled()
  })
})
