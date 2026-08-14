import { computed, reactive, toValue } from 'vue'

import { passwordPolicyChecks } from '@/lib/password'

/**
 * State for the three-field "change your password" form.
 *
 * Holds the fields, the client-side mirror of the backend policy, and the two
 * cross-field rules the backend enforces in UserUpdateMeCredentials, so the
 * dialog and the mandatory password screen cannot drift apart.
 *
 * @param {object|import('vue').Ref<object>} identifiers - The account's email and username, used by the policy checks. May be a ref or getter.
 * @returns {object} Reactive form state and helpers.
 */
export function usePasswordChangeForm(identifiers) {
  const password = reactive({
    current: null,
    new: null,
    verify: null
  })

  const checks = computed(() => {
    const { email = null, username = null } = toValue(identifiers) ?? {}
    return passwordPolicyChecks(password.new ?? '', { email, username })
  })

  // Only surfaced once the user has typed something: an untouched field has not
  // failed anything yet.
  const policyError = computed(() => {
    if (!password.new) return null
    return checks.value.find((check) => !check.ok)?.error ?? null
  })

  const mismatch = computed(
    () => !!password.new && !!password.verify && password.new !== password.verify
  )

  // Mirrors SamePasswordException. Caught here rather than on submit because it
  // is the likeliest rejection for someone who was asked to change their
  // password, and a round trip would cost them rate-limit budget.
  const sameAsCurrent = computed(
    () => !!password.new && !!password.current && password.new === password.current
  )

  const invalid = computed(
    () =>
      !password.current ||
      !password.new ||
      !password.verify ||
      !!policyError.value ||
      mismatch.value ||
      sameAsCurrent.value
  )

  const reset = () => {
    password.current = null
    password.new = null
    password.verify = null
  }

  return { password, checks, policyError, mismatch, sameAsCurrent, invalid, reset }
}
