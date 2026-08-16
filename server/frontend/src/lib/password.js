// Client-side mirror of the backend password policy
// (UserManager.validate_password). Kept in sync so users get instant feedback;
// the backend remains the source of truth and re-validates on submit.
//
// The rules, their order and their messages all match the backend. The
// blocklist does not: the browser carries the head of it, so this reports no
// false positives but does not catch every entry the backend does. A password
// this accepts can still be refused on submit, which is why every form using
// these checks also surfaces the server's message.

import commonPasswordsFile from './common-passwords.txt?raw'

export const MIN_PASSWORD_LENGTH = 12

// Identifiers shorter than this are not checked for containment, matching the
// backend, to avoid false positives on short email/username fragments.
const MIN_IDENTIFIER_LENGTH = 4

// Generated; see tooling/vendor-common-passwords.py. Lowercased entries, one
// per line, with '#' comment lines.
const COMMON_PASSWORDS = new Set(
  commonPasswordsFile
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'))
)

/**
 * Whether a password is among the commonly used ones the browser knows about.
 *
 * @param {string} password - The candidate password.
 * @returns {boolean} True when the password is a known common one.
 */
export function isCommonPassword(password) {
  return COMMON_PASSWORDS.has(String(password ?? '').toLowerCase())
}

/**
 * Evaluate a candidate password against every policy rule.
 *
 * Returns all rules rather than the first failure, so a form can show what is
 * required before the user runs into it.
 *
 * @param {string} password - The candidate password.
 * @param {object} [identifiers] - Optional account identifiers to check against.
 * @param {string} [identifiers.email] - The account email.
 * @param {string} [identifiers.username] - The account username.
 * @returns {Array<{id: string, label: string, error: string, ok: boolean}>} The rules, in the order the backend applies them.
 */
export function passwordPolicyChecks(password, { email = null, username = null } = {}) {
  const candidate = String(password ?? '')
  const lowered = candidate.toLowerCase()
  const emailLocal = email ? email.split('@')[0].toLowerCase() : ''
  const checks = [
    {
      id: 'length',
      label: `At least ${MIN_PASSWORD_LENGTH} characters`,
      error: `Password must be at least ${MIN_PASSWORD_LENGTH} characters long.`,
      // Count codepoints, not UTF-16 units, because the backend's len() counts
      // codepoints: .length would tick this rule green for twelve UTF-16 units
      // of six astral characters that the server then refuses as six.
      ok: [...candidate].length >= MIN_PASSWORD_LENGTH
    },
    {
      id: 'common',
      label: 'Not a commonly used password',
      error: 'This password is among the most commonly used ones. Please choose a different one.',
      ok: !isCommonPassword(candidate)
    },
    {
      id: 'email',
      label: 'Does not contain your email address',
      error: 'Password must not contain your email address.',
      ok: !(emailLocal.length >= MIN_IDENTIFIER_LENGTH && lowered.includes(emailLocal))
    },
    {
      id: 'username',
      label: 'Does not contain your username',
      error: 'Password must not contain your username.',
      ok: !(
        username &&
        username.length >= MIN_IDENTIFIER_LENGTH &&
        lowered.includes(username.toLowerCase())
      )
    }
  ]
  // An empty field has not failed the content rules yet, only the length one;
  // reporting "not a commonly used password" as satisfied for "" would be
  // meaningless either way, but claiming it failed would be wrong.
  return checks
}

/**
 * Validate a candidate password against the policy.
 *
 * @param {string} password - The candidate password.
 * @param {object} [identifiers] - Optional account identifiers to check against.
 * @param {string} [identifiers.email] - The account email.
 * @param {string} [identifiers.username] - The account username.
 * @returns {string|null} A human-readable error message, or null if valid.
 */
export function passwordPolicyError(password, identifiers = {}) {
  const failed = passwordPolicyChecks(password, identifiers).find((check) => !check.ok)
  return failed ? failed.error : null
}
