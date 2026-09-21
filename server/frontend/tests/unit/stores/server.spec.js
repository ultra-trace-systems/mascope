import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// The capabilities of the server a tab talks to are read at sign-in. An older
// server announces none, which must read as "not supported" rather than fail.

const loginCallbacks = []

vi.mock('@/api', () => ({ api: { http: { get: vi.fn() } } }))
vi.mock('@/stores/auth', () => ({
  useAuth: () => ({ onLogin: (callback) => loginCallbacks.push(callback) })
}))

let api
let useServer

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  loginCallbacks.length = 0
  ;({ api } = await import('@/api'))
  ;({ useServer } = await import('@/stores/server'))
})

describe('server store', () => {
  it('reads what the server announces when a person signs in', async () => {
    api.http.get.mockResolvedValue({
      version: 'v2.0.0',
      capabilities: { files_uploads_without_ionization_token: true }
    })
    const server = useServer()

    await loginCallbacks[0]()

    expect(api.http.get.mock.calls[0][0]).toBe('/version')
    expect(server.can('files_uploads_without_ionization_token')).toBe(true)
    expect(server.can('files_something_else')).toBe(false)
  })

  it('takes a server that announces nothing for one that can do nothing', async () => {
    api.http.get.mockResolvedValue({ version: 'v1.8.0' })
    const server = useServer()

    await server.load()

    expect(server.capabilities).toEqual({})
    expect(server.can('files_uploads_without_ionization_token')).toBe(false)
  })

  it('takes a server it cannot ask for one that can do nothing', async () => {
    api.http.get.mockRejectedValue(new Error('502'))
    const server = useServer()

    await server.load()

    expect(server.can('files_uploads_without_ionization_token')).toBe(false)
  })
})
