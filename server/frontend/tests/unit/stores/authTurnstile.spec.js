import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// Same stubs as the sibling auth specs: the store registers a socket listener at
// setup and reads the profile over http.
const { socket, http } = vi.hoisted(() => ({
  socket: {
    on: vi.fn(),
    addSubscription: vi.fn(),
    removeSubscription: vi.fn(),
    resubscribe: vi.fn(),
    renew: vi.fn()
  },
  http: { get: vi.fn(), post: vi.fn() }
}))

vi.mock('@/api', () => ({ api: { socket, http } }))

import { useAuth } from '@/stores/auth'

const CREDENTIALS = { email: 'a@b.c', password: 'correct horse battery staple' }

describe('auth store: Turnstile', () => {
  let auth

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    auth = useAuth()
  })

  it('reads the site key from the login options', async () => {
    http.get.mockResolvedValueOnce({ turnstile_site_key: 'site-key-1' })
    await auth.loadLoginOptions()

    expect(http.get).toHaveBeenCalledWith(
      '/auth/login/options',
      expect.objectContaining({ use: 'read' })
    )
    expect(auth.turnstileSiteKey).toBe('site-key-1')
  })

  it('has no site key when the site has Turnstile off', async () => {
    http.get.mockResolvedValueOnce({ turnstile_site_key: null })
    await auth.loadLoginOptions()
    expect(auth.turnstileSiteKey).toBeNull()
  })

  it('has no site key when the options cannot be read', async () => {
    http.get.mockRejectedValueOnce(new Error('HTTP 500'))
    await auth.loadLoginOptions()
    expect(auth.turnstileSiteKey).toBeNull()
  })

  it('sends the token in the field the server reads', async () => {
    http.post.mockResolvedValueOnce(undefined)
    await auth.login({ ...CREDENTIALS, turnstileToken: 'token-1' })

    const params = http.post.mock.calls[0][1]
    expect(params.get('cf-turnstile-response')).toBe('token-1')
    expect(params.get('username')).toBe(CREDENTIALS.email)
  })

  it('sends no token field without a token', async () => {
    http.post.mockResolvedValueOnce(undefined)
    await auth.login(CREDENTIALS)

    const params = http.post.mock.calls[0][1]
    expect(params.has('cf-turnstile-response')).toBe(false)
  })
})
