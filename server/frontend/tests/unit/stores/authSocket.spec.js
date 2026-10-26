import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// What signing in and out does to the tab's socket. Same stubs as the sibling
// auth specs, with the calls recorded in order.
const { socket, http, calls } = vi.hoisted(() => {
  const calls = []
  const record = (name) => vi.fn(async () => calls.push(name))
  return {
    calls,
    socket: {
      on: vi.fn(),
      addSubscription: vi.fn(),
      removeSubscription: vi.fn(),
      resubscribe: record('resubscribe'),
      renew: record('renew')
    },
    http: { get: record('identify'), post: vi.fn() }
  }
})

vi.mock('@/api', () => ({ api: { socket, http } }))

import { useAuth } from '@/stores/auth'

const CREDENTIALS = { email: 'a@b.c', password: 'correct horse battery staple' }

describe('auth store: the socket', () => {
  let auth

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    calls.length = 0
    auth = useAuth()
  })

  it('opens a new socket after signing out, before reading the profile', async () => {
    http.post.mockResolvedValueOnce(undefined)
    await auth.logout()

    expect(calls).toEqual(['renew', 'identify'])
  })

  it('keeps the socket when the sign-out is refused', async () => {
    // The session, and so the socket's, is still the account's.
    http.post.mockRejectedValueOnce(new Error('HTTP 500'))
    await expect(auth.logout()).rejects.toThrow()

    expect(socket.renew).not.toHaveBeenCalled()
  })

  it('asks for its rooms again on signing in', async () => {
    http.post.mockResolvedValueOnce(undefined)
    await auth.login(CREDENTIALS)

    expect(calls).toEqual(['resubscribe', 'identify'])
  })

  it('asks for its rooms only once the second factor is accepted', async () => {
    http.post.mockResolvedValueOnce({ mfaRequired: true })
    await auth.login(CREDENTIALS)
    expect(socket.resubscribe).not.toHaveBeenCalled()

    http.post.mockResolvedValueOnce(undefined)
    await auth.verifyMfa('123456')

    expect(calls).toEqual(['resubscribe', 'identify'])
  })

  it('asks for no rooms when the sign-in fails', async () => {
    http.post.mockRejectedValueOnce(new Error('HTTP 400'))
    await expect(auth.login(CREDENTIALS)).rejects.toThrow()

    expect(socket.resubscribe).not.toHaveBeenCalled()
  })
})
