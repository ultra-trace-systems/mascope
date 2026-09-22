import { describe, it, expect, beforeEach, vi } from 'vitest'
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

const add = (store, name) => {
  try {
    store.get().addFile({ name, type: 'application/octet-stream', data: new Blob(['x']) })
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
