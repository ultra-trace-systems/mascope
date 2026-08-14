import { describe, it, expect, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// The store registers a socket listener at setup and reads the profile over
// http; stubs are all it needs. Declared through vi.hoisted because vi.mock is
// lifted above ordinary top-level declarations.
const { socket, http } = vi.hoisted(() => ({
  socket: { on: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() },
  http: { get: vi.fn(), post: vi.fn() }
}))

vi.mock('@/api', () => ({ api: { socket, http } }))

import { useAuth } from '@/stores/auth'

const GATED_USER = {
  id: 7,
  email: 'a@b.c',
  must_change_password: true,
  password_change_reason: 'policy'
}
const FREE_USER = {
  id: 7,
  email: 'a@b.c',
  must_change_password: false,
  password_change_reason: null
}

describe('auth store: forced password change', () => {
  let auth

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    auth = useAuth()
  })

  /** Resolve the next identify() with `user` and let the watcher run. */
  const identifyAs = async (user) => {
    http.get.mockResolvedValueOnce(user)
    await auth.identify()
    await nextTick()
  }

  it('exposes the requirement and its reason', async () => {
    await identifyAs(GATED_USER)
    expect(auth.mustChangePassword).toBe(true)
    expect(auth.passwordChangeReason).toBe('policy')
  })

  it('reports no requirement for an unflagged user', async () => {
    await identifyAs(FREE_USER)
    expect(auth.mustChangePassword).toBe(false)
    expect(auth.passwordChangeReason).toBeNull()
  })

  it('does not fire login callbacks while the user is held at the gate', async () => {
    // The app's ~20 root stores register here. Letting them load behind the
    // password screen would fire a burst of requests that all get refused.
    const callback = vi.fn()
    auth.onLogin(callback)
    await identifyAs(GATED_USER)
    expect(callback).not.toHaveBeenCalled()
  })

  it('fires login callbacks exactly once when the requirement clears', async () => {
    const callback = vi.fn()
    auth.onLogin(callback)
    await identifyAs(GATED_USER)
    await identifyAs(FREE_USER)
    expect(callback).toHaveBeenCalledTimes(1)

    // A later profile refresh for the same user must not re-fire them.
    await identifyAs({ ...FREE_USER })
    expect(callback).toHaveBeenCalledTimes(1)
  })

  it('subscribes a gated user to their own socket room', async () => {
    // That room is how a tab hears the change that lets it back in, so the
    // subscription must not wait for the gate to clear.
    await identifyAs(GATED_USER)
    expect(socket.addSubscription).toHaveBeenCalledWith('user-7')
  })

  it('does not re-subscribe when the requirement clears', async () => {
    await identifyAs(GATED_USER)
    socket.addSubscription.mockClear()
    socket.removeSubscription.mockClear()
    await identifyAs(FREE_USER)
    expect(socket.addSubscription).not.toHaveBeenCalled()
    expect(socket.removeSubscription).not.toHaveBeenCalled()
  })

  it('reports only the first rejection so a burst yields one notice', async () => {
    await identifyAs(GATED_USER)
    http.get.mockResolvedValue(GATED_USER)
    expect(auth.requirePasswordChange()).toBe(true)
    expect(auth.requirePasswordChange()).toBe(false)
    expect(auth.requirePasswordChange()).toBe(false)
  })

  it('re-arms the notice only after the requirement has cleared', async () => {
    await identifyAs(GATED_USER)
    http.get.mockResolvedValue(GATED_USER)
    expect(auth.requirePasswordChange()).toBe(true)

    // A late rejection arriving while still gated must not re-arm it.
    expect(auth.requirePasswordChange()).toBe(false)

    await identifyAs(FREE_USER)
    await identifyAs(GATED_USER)
    http.get.mockResolvedValue(GATED_USER)
    expect(auth.requirePasswordChange()).toBe(true)
  })

  it('treats a signed-out response as anonymous, not as gated', async () => {
    http.get.mockResolvedValueOnce(null)
    await auth.identify()
    await nextTick()
    expect(auth.user).toBe(false)
    expect(auth.mustChangePassword).toBe(false)
  })
})
