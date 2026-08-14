import { computed, ref, watch, onMounted } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'

export const useAuth = defineStore('app.auth', () => {
  const user = ref(null)
  const requiresOwner = ref(null)

  // A user held behind the mandatory password change is authenticated but not
  // yet "in" the app. Derived from the user object rather than tracked
  // separately, so the template branch and the watch below read one source and
  // cannot disagree. /users/me stays reachable while it is set, and is the only
  // thing that clears it.
  const mustChangePassword = computed(() => !!user.value?.must_change_password)
  const passwordChangeReason = computed(() => user.value?.password_change_reason ?? null)

  // One-shot latch for the "you need a new password" notice, so a burst of
  // store syncs all being refused at once yields one message rather than
  // twenty.
  const gateNoticeShown = ref(false)

  // Initial auth checks
  onMounted(async () => {
    await identify()
  })

  const identify = async () => {
    user.value =
      (await api.http.get('/users/me', {
        type: 'identify_user',
        use: 'auth',
        validateStatus: (status) => status < 500
      })) ?? false
    if (!user.value || !user.value.must_change_password) {
      gateNoticeShown.value = false
    }
  }

  /**
   * React to an API call refused because the account owes a password change.
   *
   * Re-reads the profile so the app swaps to the password screen. Deliberately
   * not cleared by identify() while the flag is still set: a late rejection
   * arriving after the re-read would otherwise re-arm the notice.
   *
   * @returns {boolean} True only for the first rejection, so the caller
   * notifies once.
   */
  const requirePasswordChange = () => {
    const first = !gateNoticeShown.value
    gateNoticeShown.value = true
    identify()
    return first
  }

  const login = async ({ email, password }) => {
    const params = new URLSearchParams()
    params.append('grant_type', 'password')
    params.append('username', email)
    params.append('password', password)

    await api.http.post('/auth/login', params, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      type: 'user_sign_in',
      use: 'auth'
    })
    await identify()
  }

  const logout = async () => {
    await api.http.post(
      `/auth/logout`,
      {},
      {
        type: 'user_sign_out',
        use: 'auth'
      }
    )
    await identify()
  }

  // --- First owner management ---
  const checkFirstOwner = async () => {
    const response = await api.http.get('/users/first-owner/status', {
      type: 'first_owner_status',
      use: 'auth'
    })
    requiresOwner.value = response?.status === 'available'
  }

  const signupFirstOwner = async ({ email, username, password, serverSecret }) => {
    await api.http.post(
      '/users/first-owner',
      { email, username, password, server_secret: serverSecret },
      {
        type: 'first_owner_sign_up',
        use: 'create'
      }
    )
    await checkFirstOwner()
  }
  // --- Login callback system (pub-sub pattern) ---
  // Stores register callbacks via auth.onLogin(() => sync())
  // When user actually logs in, all registered callbacks fire
  const handlers = ref([])

  function onLogin(callback) {
    handlers.value.push({ callback })
  }

  // The ID of a user who may actually use the app. A user held behind the
  // mandatory password change is authenticated but gated, so their stores must
  // not load behind the password screen - and must load exactly once when it
  // clears.
  const sessionId = (candidate) =>
    candidate && typeof candidate === 'object' && !candidate.must_change_password
      ? candidate.id
      : null

  // Watch user changes for side effects
  watch(
    () => user.value,
    async (newUser, oldUser) => {
      // Compare by ID, not object references (prevents false positives on profile updates)
      const oldId = oldUser && typeof oldUser === 'object' ? oldUser.id : null
      const newId = newUser && typeof newUser === 'object' ? newUser.id : null
      // Fire login callbacks ONLY when the user becomes able to use the app:
      // either a plain sign-in, or a gated user completing their password change.
      if (sessionId(newUser) && !sessionId(oldUser)) {
        handlers.value.forEach(({ callback }) => callback())
      }

      // Socket subscriptions to user room - if user ID changed. Keyed on the
      // raw ID, not sessionId: a gated tab must stay in its own room, since
      // that is how it hears about the change that lets it back in.
      if (oldId !== newId) {
        if (oldId) {
          api.socket.removeSubscription(`user-${oldId}`)
        }
        if (newId) {
          api.socket.addSubscription(`user-${newId}`)
        }
      }

      // Check first owner status only when no authenticated user found
      if (newUser === false && requiresOwner.value === null) {
        await checkFirstOwner()
      }
    }
  )

  // Listen for profile updates via socket and refresh user data
  api.socket.on('user_me_updated', identify)

  return {
    user,
    requiresOwner,
    mustChangePassword,
    passwordChangeReason,
    identify,
    requirePasswordChange,
    login,
    logout,
    checkFirstOwner,
    signupFirstOwner,
    onLogin
  }
})
