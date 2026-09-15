import { ref } from 'vue'

import { needsMfaReauth } from '@/api/utils'

/**
 * The step-up-and-retry protocol shared by every control that can be refused
 * with `mfa_reauth_required` - regenerating an access token, approving an agent
 * pairing, resetting another account's second factor. Owns the pending action
 * and the prompt's visibility, so each caller supplies only the action and how
 * to report a failure of the replay.
 *
 * Wire the returned `reauthVisible` to a `<DialogMfaReauth>` and its `verified`
 * event to the handler `onVerified` builds.
 *
 * @returns {{
 *   reauthVisible: import('vue').Ref<boolean>,
 *   runWithReauth: (action: Function) => Promise<*>,
 *   onVerified: (onError?: (e: *) => void) => (() => Promise<void>)
 * }}
 */
export function useMfaReauth() {
  const reauthVisible = ref(false)
  const pendingAction = ref(null)

  // Run `action`; if the server asks for a fresh code, remember it and open the
  // prompt. Any other error propagates, so the caller handles its own failures.
  const runWithReauth = async (action) => {
    try {
      return await action()
    } catch (e) {
      if (!needsMfaReauth(e)) throw e
      pendingAction.value = action
      reauthVisible.value = true
    }
  }

  // Build the handler for the reauth dialog's `verified` event: replay the
  // stored action exactly once, never prompting again - the code was just
  // accepted, so a second refusal is a real failure. `onError` receives a
  // failure of that replay.
  const onVerified = (onError) => async () => {
    const action = pendingAction.value
    pendingAction.value = null
    if (!action) return
    try {
      await action()
    } catch (e) {
      if (onError) onError(e)
    }
  }

  return { reauthVisible, runWithReauth, onVerified }
}
