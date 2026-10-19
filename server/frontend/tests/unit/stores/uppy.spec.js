import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// Which files the browser lets through to the upload. A name must start with
// an instrument the server can place; it must also carry an ionization mode
// token, unless the server announces that it keeps a file without one for
// someone to choose its chemistry.

const state = vi.hoisted(() => ({ tokenless: false, pending: false, push: vi.fn() }))

vi.mock('@/api', () => ({ api: { socket: { id: 'sid' } } }))
vi.mock('@/lib/runtime.js', () => ({ runtime: { api_path: '' } }))
vi.mock('@/lib/features', () => ({ maxUploadBytes: 10 * 1024 ** 3 }))
vi.mock('@/stores/auth', () => ({ useAuth: () => ({ requirePasswordChange: vi.fn() }) }))
vi.mock('@/stores/data/modules/instrument', () => ({
  useInstrument: () => ({ typeOf: (name) => (name === 'Orbi-1' ? 'orbi' : null) })
}))
vi.mock('@/stores/data/modules/ionization', () => ({
  useIonizationMode: () => ({ list: [{ ionization_mode_token: 'NO3' }], pending: state.pending })
}))
vi.mock('@/stores/server', () => ({
  TOKENLESS_UPLOADS: 'files_uploads_without_ionization_token',
  useServer: () => ({
    can: (name) => state.tokenless && name === 'files_uploads_without_ionization_token'
  })
}))
vi.mock('@/stores/ui', () => ({ useUi: () => ({ notification: { push: state.push } }) }))

import { useUppy } from '@/stores/uppy'

const add = (store, name, meta) => {
  try {
    store.get().addFile({ name, type: 'application/octet-stream', data: new Blob(['x']), meta })
    return true
  } catch {
    return false
  }
}

describe('upload store: which names go through', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    state.tokenless = false
    state.pending = false
  })

  it('refuses a name without a token when the server cannot keep it', () => {
    const store = useUppy()

    expect(add(store, 'Orbi-1_2026.09.21_001.raw')).toBe(false)
    expect(store.invalidFiles.map(({ name }) => name)).toEqual(['Orbi-1_2026.09.21_001.raw'])
  })

  it('lets a name without a token through when the server keeps such a file', () => {
    state.tokenless = true
    const store = useUppy()

    expect(add(store, 'Orbi-1_2026.09.21_001.raw')).toBe(true)
    expect(store.invalidFiles).toEqual([])
    expect(state.push).toHaveBeenCalledWith(
      expect.objectContaining({
        status: 'info',
        message: expect.stringContaining('Orbi-1_2026.09.21_001.raw carries no ionization mode')
      })
    )
  })

  it('says nothing about a name that carries a token', () => {
    state.tokenless = true
    const store = useUppy()

    expect(add(store, 'Orbi-1_2026.09.21_NO3_001.raw')).toBe(true)
    expect(state.push).not.toHaveBeenCalled()
  })

  it('still refuses a name that places no instrument', () => {
    state.tokenless = true
    const store = useUppy()

    expect(add(store, 'Nowhere_2026.09.21_NO3_001.raw')).toBe(false)
    expect(store.invalidFiles).toHaveLength(1)
  })

  it('lets a file through when the dialog reports the instrument with it', () => {
    state.tokenless = true
    const store = useUppy()

    // The name places no instrument, and says nothing of its class either.
    // The server files a reported instrument under any valid name.
    expect(add(store, 'Nowhere_2026.09.21_NO3_001.raw', { instrument: 'Lab-2' })).toBe(true)
    expect(store.invalidFiles).toEqual([])
  })

  it('refuses a reported instrument the server would not accept', () => {
    state.tokenless = true
    const store = useUppy()

    // An underscore separates the instrument from the rest of a name, so it
    // cannot be part of the instrument.
    expect(add(store, 'Nowhere_2026.09.21_NO3_001.raw', { instrument: 'Lab_2' })).toBe(false)
    expect(store.invalidFiles).toHaveLength(1)
  })
})

describe('upload store: the note about names without a token', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    state.tokenless = true
    state.pending = false
  })

  const files = (...names) =>
    names.map((name) => ({ name, type: 'application/octet-stream', data: new Blob([name]) }))

  it('notes a drop once, whatever its size', () => {
    const store = useUppy()

    store.get().addFiles(files('Orbi-1_a.raw', 'Orbi-1_b.raw', 'Orbi-1_c.raw', 'Orbi-1_NO3_d.raw'))

    expect(state.push).toHaveBeenCalledTimes(1)
    expect(state.push.mock.calls[0][0].message).toContain('3 files carry no ionization mode token')
  })

  it('says nothing about a file Uppy turns away', () => {
    const store = useUppy()
    store.get().addFiles(files('Orbi-1_a.raw'))

    // The same file again is a duplicate Uppy refuses after this store has
    // let it through.
    try {
      store.get().addFiles(files('Orbi-1_a.raw'))
    } catch {
      // Refused, as it should be.
    }

    expect(state.push).toHaveBeenCalledTimes(1)
  })

  it('notes the files the upload dialog hands back once, too', () => {
    const store = useUppy()

    store.addFromDialog(files('Orbi-1_a.raw', 'Orbi-1_b.raw', 'Orbi-1_c.raw'))

    expect(state.push).toHaveBeenCalledTimes(1)
    expect(state.push.mock.calls[0][0].message).toContain('3 files carry no ionization mode token')
    expect(store.get().getFiles()).toHaveLength(3)
  })

  it('leaves it to Uppy to report a file it refuses from the dialog', () => {
    const store = useUppy()
    store.addFromDialog(files('Orbi-1_a.raw'))

    expect(() => store.addFromDialog(files('Orbi-1_a.raw', 'Orbi-1_b.raw'))).not.toThrow()
    expect(store.get().getFiles()).toHaveLength(2)
  })

  it('says nothing while the ionization modes are still loading', () => {
    state.pending = true
    const store = useUppy()

    store.get().addFiles(files('Orbi-1_a.raw'))

    expect(state.push).not.toHaveBeenCalled()
  })
})

describe('upload store: the zone an acquisition time is read in', () => {
  // A raw file records its acquisition time as the instrument PC's wall
  // clock, with no offset. The server reads it in the zone the upload
  // reports: an agent's for the instrument PC, else the browser's, which it
  // ranks below and records under its own name, since the browser is
  // wherever the person uploading sits. With none reported it reads it in
  // its own zone, UTC in production, and a file uploaded from a browser east
  // of UTC was stored hours late.
  //
  // What matters is what reaches the server, so these tests read the tus
  // request itself, through a request stack of their own: the metadata
  // travels base64-encoded in the Upload-Metadata header of the POST that
  // creates the upload.
  let zone
  let requests

  const stack = {
    getName: () => 'test',
    createRequest(method, url) {
      const headers = {}
      return {
        getMethod: () => method,
        getURL: () => url,
        setHeader: (name, value) => {
          headers[name] = value
        },
        getHeader: (name) => headers[name],
        setProgressHandler() {},
        send() {
          requests.push({ method, url, headers })
          return new Promise(() => {}) // the request is read, never answered
        },
        abort: () => Promise.resolve(),
        getUnderlyingObject: () => null
      }
    }
  }

  const metadataSent = async () => {
    await vi.waitFor(() => expect(requests).toHaveLength(1))
    return Object.fromEntries(
      requests[0].headers['Upload-Metadata'].split(',').map((pair) => {
        const [key, value] = pair.split(' ')
        return [key, value ? atob(value) : '']
      })
    )
  }

  // Under Vitest the tus client is its Node build, whose file reader takes
  // a Buffer, not a Blob; a reader of the test's own keeps the files as the
  // other tests make them. The request is read before any byte is sent.
  const fileReader = {
    openFile: async (input) => ({
      size: input.size,
      slice: async () => ({ value: null, done: true }),
      close() {}
    })
  }

  const storeOnTestStack = () => {
    const store = useUppy()
    store.get().getPlugin('Tus').setOptions({ httpStack: stack, fileReader })
    return store
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    state.tokenless = true
    state.pending = false
    requests = []
    zone = vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions')
    zone.mockReturnValue({ timeZone: 'Europe/Vienna' })
  })

  afterEach(() => {
    zone.mockRestore()
  })

  it('sends the browser zone under its own name, next to the instrument the dialog reports', async () => {
    const store = storeOnTestStack()
    store.addFromDialog([
      {
        name: 'Nowhere_2026.09.21_NO3_001.raw',
        data: new Blob(['x']),
        meta: { instrument: 'Lab-2' }
      }
    ])

    store.get().upload()

    expect(await metadataSent()).toMatchObject({
      filename: 'Nowhere_2026.09.21_NO3_001.raw',
      instrument: 'Lab-2',
      browser_timezone: 'Europe/Vienna'
    })
    // Not as the agent's zone: the server ranks and labels the two apart.
    expect(requests[0].headers['Upload-Metadata']).not.toMatch(/(^|,)timezone /)
  })

  it('reads the zone as the upload starts, not when the app did', async () => {
    const store = storeOnTestStack()
    zone.mockReturnValue({ timeZone: 'America/Denver' })
    add(store, 'Orbi-1_2026.09.21_NO3_001.raw')

    store.get().upload()

    expect((await metadataSent()).browser_timezone).toBe('America/Denver')
  })

  it('sends no zone when the browser names only a placeholder', async () => {
    const store = storeOnTestStack()
    zone.mockReturnValue({ timeZone: 'UTC' })
    add(store, 'Orbi-1_2026.09.21_NO3_001.raw')

    store.get().upload()

    const metadata = await metadataSent()
    expect(metadata.filename).toBe('Orbi-1_2026.09.21_NO3_001.raw')
    expect(metadata).not.toHaveProperty('browser_timezone')
  })

  it('lets every meta field a file carries through to the server', () => {
    // The default, pinned: a list of names would send each name whether the
    // file carries it or not, an absent one as the string "undefined", and
    // the server reads a reported instrument off that field.
    const store = useUppy()

    expect(store.get().getPlugin('Tus').opts.allowedMetaFields).toBe(true)
  })
})
