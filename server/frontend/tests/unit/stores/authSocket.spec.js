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
    http: {
      // The profile read only: the first-owner check a signed-out tab makes
      // is no step of signing in or out.
      get: vi.fn(async (url) => (url === '/users/me' ? calls.push('identify') : undefined)),
      post: vi.fn()
    }
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

  // The profile read of a browser with no session any more.
  const signedOutProfile = async () => calls.push('identify') && false

  // The handler the store registered for the server's sign-out event.
  const signedOut = () => socket.on.mock.calls.find(([event]) => event === 'signed_out')[1]

  it('opens a new socket and reads the profile when another tab signs out', async () => {
    // The browser has no session any more: the sign-in screen.
    http.get.mockImplementationOnce(signedOutProfile)
    await signedOut()()

    expect(calls).toEqual(['renew', 'identify'])
  })

  it('asks for its rooms again when the browser has signed in meanwhile', async () => {
    // The new connection was signed in from the cookie another tab set.
    http.get.mockImplementationOnce(async () => calls.push('identify') && { id: 7 })
    await signedOut()()

    expect(calls).toEqual(['renew', 'identify', 'resubscribe'])
  })

  it('handles a burst of sign-out events once', async () => {
    http.get.mockImplementationOnce(signedOutProfile)
    await Promise.all([signedOut()(), signedOut()()])

    expect(calls).toEqual(['renew', 'identify'])
  })

  it('ignores the event for its own sign-out', async () => {
    // The server tells the signing-out tab's socket too, while the request is
    // still in flight; the sign-out renews it once.
    http.post.mockImplementationOnce(async () => {
      await signedOut()()
    })
    await auth.logout()

    expect(calls).toEqual(['renew', 'identify'])
  })

  it('heeds the event again once its own sign-out is over', async () => {
    http.post.mockRejectedValueOnce(new Error('HTTP 500'))
    await expect(auth.logout()).rejects.toThrow()

    http.get.mockImplementationOnce(signedOutProfile)
    await signedOut()()

    expect(calls).toEqual(['renew', 'identify'])
  })

  it('asks for no rooms when the sign-in fails', async () => {
    http.post.mockRejectedValueOnce(new Error('HTTP 400'))
    await expect(auth.login(CREDENTIALS)).rejects.toThrow()

    expect(socket.resubscribe).not.toHaveBeenCalled()
  })
})
