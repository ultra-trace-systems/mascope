/**
 * How the Manage users table shows an account's second factor.
 *
 * Read off the server's derived fields rather than recomputed from the role, so
 * the table shows the policy the server enforces: `mfa_exempt` marks an account
 * the requirement never applies to (a machine account, which never signs in at
 * a browser), and `mfa_enrollment_required` one still held at enrolment.
 * Exempt wins over pending, so an exempt account can never be counted among
 * those still owing an enrolment.
 *
 * @param {object} user - A user as the users API returns it.
 * @returns {{ state: 'on'|'exempt'|'pending'|'off', label: string|null, tooltip: string }}
 */
export function twoFactorDisplay(user) {
  if (user?.mfa_enabled) {
    return { state: 'on', label: null, tooltip: 'Two-factor authentication is on' }
  }
  if (user?.mfa_exempt) {
    return {
      state: 'exempt',
      label: 'exempt',
      tooltip: 'Not required: a machine account never signs in interactively'
    }
  }
  if (user?.mfa_enrollment_required) {
    return {
      state: 'pending',
      label: 'pending',
      tooltip: 'Required for this role, not set up yet'
    }
  }
  return { state: 'off', label: null, tooltip: 'Not set up' }
}
