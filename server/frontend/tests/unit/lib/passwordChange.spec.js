import { describe, it, expect } from 'vitest'
import { ref } from 'vue'

import { usePasswordChangeForm } from '@/lib/passwordChange'

const IDENTIFIERS = { email: 'chemist@example.com', username: 'labuser' }
const GOOD = 'sixteen tonnes of quartz'

/** Fill all three fields. */
function fill(form, { current = 'nine bushels of slate', next = GOOD, verify = next } = {}) {
  form.password.current = current
  form.password.new = next
  form.password.verify = verify
}

describe('usePasswordChangeForm', () => {
  it('starts invalid with empty fields and no error shown', () => {
    const form = usePasswordChangeForm(IDENTIFIERS)
    expect(form.invalid.value).toBe(true)
    // Nothing has been typed, so nothing has failed yet.
    expect(form.policyError.value).toBeNull()
    expect(form.mismatch.value).toBe(false)
  })

  it('accepts a compliant password', () => {
    const form = usePasswordChangeForm(IDENTIFIERS)
    fill(form)
    expect(form.policyError.value).toBeNull()
    expect(form.invalid.value).toBe(false)
  })

  it('reports a policy failure and stays invalid', () => {
    const form = usePasswordChangeForm(IDENTIFIERS)
    fill(form, { next: 'short' })
    expect(form.policyError.value).toBe('Password must be at least 12 characters long.')
    expect(form.invalid.value).toBe(true)
  })

  it('reports a mismatch between the new password and its confirmation', () => {
    const form = usePasswordChangeForm(IDENTIFIERS)
    fill(form, { next: GOOD, verify: 'something else entirely' })
    expect(form.mismatch.value).toBe(true)
    expect(form.invalid.value).toBe(true)
  })

  it('refuses a new password identical to the current one', () => {
    // Mirrors the backend's SamePasswordException. Caught here so the user does
    // not spend a round trip and a rate-limit token discovering it.
    const form = usePasswordChangeForm(IDENTIFIERS)
    fill(form, { current: GOOD, next: GOOD })
    expect(form.sameAsCurrent.value).toBe(true)
    expect(form.invalid.value).toBe(true)
  })

  it('tracks identifiers reactively', () => {
    const identifiers = ref({ email: 'chemist@example.com', username: 'labuser' })
    const form = usePasswordChangeForm(identifiers)
    fill(form, { next: 'the labuser way' })
    expect(form.policyError.value).toBe('Password must not contain your username.')

    // A different account: the same candidate no longer echoes an identifier.
    identifiers.value = { email: 'other@example.com', username: 'somebody' }
    expect(form.policyError.value).toBeNull()
  })

  it('accepts a getter for the identifiers', () => {
    const form = usePasswordChangeForm(() => IDENTIFIERS)
    fill(form, { next: 'my chemist rocks' })
    expect(form.policyError.value).toBe('Password must not contain your email address.')
  })

  it('exposes the full checklist for display', () => {
    const form = usePasswordChangeForm(IDENTIFIERS)
    fill(form)
    expect(form.checks.value.map((check) => check.id)).toEqual([
      'length',
      'common',
      'email',
      'username'
    ])
    expect(form.checks.value.every((check) => check.ok)).toBe(true)
  })

  it('clears every field on reset', () => {
    const form = usePasswordChangeForm(IDENTIFIERS)
    fill(form)
    form.reset()
    expect(form.password.current).toBeNull()
    expect(form.password.new).toBeNull()
    expect(form.password.verify).toBeNull()
    expect(form.invalid.value).toBe(true)
  })
})
