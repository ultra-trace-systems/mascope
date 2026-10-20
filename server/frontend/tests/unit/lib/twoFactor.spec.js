import { describe, it, expect } from 'vitest'

import { twoFactorDisplay } from '@/lib/twoFactor'

// Under a policy covering editors, the server marks a machine account exempt
// and a human editor without a factor pending. The table has to show those as
// two different things: the operator reads "pending" as the set still owing an
// enrolment, and a machine account can never leave it.

const editor = (fields) => ({ role_name: 'editor', mfa_enabled: false, ...fields })

describe('twoFactorDisplay', () => {
  it('shows a machine account as exempt, not pending', () => {
    const display = twoFactorDisplay(
      editor({ account_type: 'machine', mfa_exempt: true, mfa_enrollment_required: false })
    )
    expect(display.state).toBe('exempt')
    expect(display.label).toBe('exempt')
    expect(display.tooltip).toMatch(/machine account/)
  })

  it('shows a human editor without a factor as pending', () => {
    const display = twoFactorDisplay(
      editor({ account_type: 'person', mfa_exempt: false, mfa_enrollment_required: true })
    )
    expect(display.state).toBe('pending')
    expect(display.label).toBe('pending')
  })

  it('lets exemption win over a pending flag', () => {
    // Defensive: should the two ever both be set, the account must not be
    // counted as owing an enrolment it cannot complete.
    expect(
      twoFactorDisplay(editor({ mfa_exempt: true, mfa_enrollment_required: true })).state
    ).toBe('exempt')
  })

  it('shows an enrolled account as on, and an uncovered one as not set up', () => {
    expect(twoFactorDisplay(editor({ mfa_enabled: true })).state).toBe('on')
    const off = twoFactorDisplay(editor({ mfa_exempt: false, mfa_enrollment_required: false }))
    expect(off.state).toBe('off')
    expect(off.label).toBeNull()
  })
})
