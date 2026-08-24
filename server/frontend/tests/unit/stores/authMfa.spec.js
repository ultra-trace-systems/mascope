import { describe, it, expect, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'
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

// The deployment requires a second factor at this account's role and it has
// none yet: authenticated, but held on the enrolment screen.
const UNENROLLED_USER = { ...USER, mfa_enrollment_required: true }
const ENROLLED_USER = { ...USER, mfa_enrollment_required: false }
const BOTH_GATES_USER = {
  ...UNENROLLED_USER,
  must_change_password: true,
  password_change_reason: 'policy'
}

describe('auth store: mandatory enrolment', () => {
  let auth

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    // clearAllMocks only clears call records: the persistent mockResolvedValue()
    // the burst tests below install would otherwise be inherited by whatever
    // test is added after them.
    http.get.mockReset()
    http.post.mockReset()
    auth = useAuth()
  })

  /** Resolve the next identify() with `user` and let the watcher run. */
  const identifyAs = async (user) => {
    http.get.mockResolvedValueOnce(user)
    await auth.identify()
    await nextTick()
  }

  it('holds an unenrolled account out of the app', async () => {
    // The app's ~20 root stores register here. Letting them load behind the
    // enrolment screen would fire a burst of requests that all get refused.
    const callback = vi.fn()
    auth.onLogin(callback)
    await identifyAs(UNENROLLED_USER)

    expect(auth.mustEnrollMfa).toBe(true)
    expect(callback).not.toHaveBeenCalled()
  })

  it('lets an enrolled account straight in', async () => {
    const callback = vi.fn()
    auth.onLogin(callback)
    await identifyAs(ENROLLED_USER)

    expect(auth.mustEnrollMfa).toBe(false)
    expect(callback).toHaveBeenCalledTimes(1)
  })

  it('shows the password screen first when the account owes both', async () => {
    // The server enforces this order too, so an account owing both replaces
    // its password before it is asked to enrol.
    await identifyAs(BOTH_GATES_USER)

    expect(auth.mustChangePassword).toBe(true)
    expect(auth.mustEnrollMfa).toBe(false)
  })

  it('hands the held user over to the enrolment screen once the password lands', async () => {
    const callback = vi.fn()
    auth.onLogin(callback)
    await identifyAs(BOTH_GATES_USER)
    await identifyAs(UNENROLLED_USER)

    expect(auth.mustChangePassword).toBe(false)
    expect(auth.mustEnrollMfa).toBe(true)
    // Clearing only the first gate must not release the stores: the user is
    // still not "in" the app.
    expect(callback).not.toHaveBeenCalled()
  })

  it('fires login callbacks exactly once when the enrolment lands', async () => {
    const callback = vi.fn()
    auth.onLogin(callback)
    await identifyAs(UNENROLLED_USER)
    expect(callback).not.toHaveBeenCalled()

    await identifyAs(ENROLLED_USER)
    expect(callback).toHaveBeenCalledTimes(1)

    // A later profile refresh for the same user must not re-fire them.
    await identifyAs({ ...ENROLLED_USER })
    expect(callback).toHaveBeenCalledTimes(1)
  })

  it('keeps an unenrolled tab in its own socket room', async () => {
    // That room is how the tab hears the enrolment landing, so the
    // subscription must not wait for the gate to clear.
    await identifyAs(UNENROLLED_USER)

    expect(socket.addSubscription).toHaveBeenCalledWith('user-7')
  })

  it('shares one profile re-read across a burst of refusals', async () => {
    // A sweep refuses every open store sync at once; each rejection calls
    // requireMfaEnrollment(), but one /users/me re-read serves them all.
    await identifyAs(UNENROLLED_USER)
    http.get.mockClear()
    http.get.mockResolvedValue(UNENROLLED_USER)
    auth.requireMfaEnrollment()
    auth.requireMfaEnrollment()
    auth.requireMfaEnrollment()

    expect(http.get).toHaveBeenCalledTimes(1)
  })

  it('releases the shared re-read once it has settled', async () => {
    // The shared slot must not wedge: a refusal arriving after the re-read
    // finished has to be able to start another one.
    await identifyAs(UNENROLLED_USER)
    http.get.mockClear()
    http.get.mockResolvedValue(UNENROLLED_USER)
    auth.requireMfaEnrollment()
    await new Promise((resolve) => setTimeout(resolve))
    auth.requireMfaEnrollment()

    expect(http.get).toHaveBeenCalledTimes(2)
  })

  it('leaves the password gate notice unspent', async () => {
    // The enrolment gate has no toast of its own - the screen is already in
    // front of the user - so it must not burn the password gate's one-shot.
    await identifyAs(UNENROLLED_USER)
    http.get.mockResolvedValue(UNENROLLED_USER)
    auth.requireMfaEnrollment()

    expect(auth.requirePasswordChange()).toBe(true)
  })

  it('signs out from the enrolment screen', async () => {
    // The only way out for a user who will not enrol, so it must work while
    // the hold is on.
    await identifyAs(UNENROLLED_USER)
    expect(auth.mustEnrollMfa).toBe(true)

    http.post.mockResolvedValueOnce(undefined)
    http.get.mockResolvedValueOnce(null)
    await auth.logout()
    await nextTick()

    expect(http.post).toHaveBeenCalledWith(
      '/auth/logout',
      {},
      expect.objectContaining({ type: 'user_sign_out' })
    )
    expect(auth.user).toBe(false)
    expect(auth.mustEnrollMfa).toBe(false)
  })
})
