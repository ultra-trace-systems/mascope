import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// Same stubs as the sibling auth spec: the store registers a socket listener at
// setup and reads the profile over http. Declared through vi.hoisted because
// vi.mock is lifted above ordinary top-level declarations.
const { socket, http } = vi.hoisted(() => ({
  socket: { on: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() },
  http: { get: vi.fn(), post: vi.fn() }
}))

vi.mock('@/api', () => ({ api: { socket, http } }))

import { useAuth } from '@/stores/auth'

const USER = { id: 7, email: 'a@b.c', must_change_password: false }
const CREDENTIALS = { email: 'a@b.c', password: 'correct horse battery staple' }

/** An axios-shaped rejection, which is what the store branches on. */
const httpError = (status) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status }
  })

describe('auth store: second factor', () => {
  let auth

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    auth = useAuth()
  })

  it('holds the sign-in at the code step when the server asks for one', async () => {
    http.post.mockResolvedValueOnce({ mfaRequired: true })
    await auth.login(CREDENTIALS)

    expect(auth.mfaPending).toBe(true)
    // The profile must not be read yet: no session exists until the code is
    // verified, so identify() would answer "not signed in" and bounce the user
    // off the code screen.
    expect(http.get).not.toHaveBeenCalled()
  })

  it('completes the sign-in directly when no second factor is owed', async () => {
    http.post.mockResolvedValueOnce(undefined)
    http.get.mockResolvedValueOnce(USER)
    await auth.login(CREDENTIALS)

    expect(auth.mfaPending).toBe(false)
    expect(http.get).toHaveBeenCalled()
  })

  it('reads the profile only after a code is accepted', async () => {
    http.post.mockResolvedValueOnce({ mfaRequired: true })
    await auth.login(CREDENTIALS)
    expect(http.get).not.toHaveBeenCalled()

    http.post.mockResolvedValueOnce(undefined)
    http.get.mockResolvedValueOnce(USER)
    await auth.verifyMfa('123456')

    expect(auth.mfaPending).toBe(false)
    expect(http.get).toHaveBeenCalled()
  })

  it('stays on the code step when the code is wrong', async () => {
    http.post.mockResolvedValueOnce({ mfaRequired: true })
    await auth.login(CREDENTIALS)

    // A wrong code is a 400: the sign-in attempt is still alive, so making the
    // user retype their password would be gratuitous.
    http.post.mockRejectedValueOnce(httpError(400))
    await expect(auth.verifyMfa('000000')).rejects.toThrow()
    expect(auth.mfaPending).toBe(true)
  })

  it('returns to the credentials step when the attempt itself expires', async () => {
    http.post.mockResolvedValueOnce({ mfaRequired: true })
    await auth.login(CREDENTIALS)

    // A 401 means the pending token is gone - expired, or spent by too many
    // wrong codes. There is nothing left to complete, so the user starts over.
    http.post.mockRejectedValueOnce(httpError(401))
    await expect(auth.verifyMfa('000000')).rejects.toThrow()
    expect(auth.mfaPending).toBe(false)
  })

  it('clears the pending step on cancel', async () => {
    http.post.mockResolvedValueOnce({ mfaRequired: true })
    await auth.login(CREDENTIALS)

    auth.cancelMfa()
    expect(auth.mfaPending).toBe(false)
  })

  it('does not leave a stale pending step across sign-ins', async () => {
    http.post.mockResolvedValueOnce({ mfaRequired: true })
    await auth.login(CREDENTIALS)
    expect(auth.mfaPending).toBe(true)

    // A second sign-in as an account without a factor must land in the app
    // rather than inherit the previous attempt's code screen.
    http.post.mockResolvedValueOnce(undefined)
    http.get.mockResolvedValueOnce(USER)
    await auth.login(CREDENTIALS)
    expect(auth.mfaPending).toBe(false)
  })
})
