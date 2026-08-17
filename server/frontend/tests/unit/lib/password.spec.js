import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import {
  MIN_PASSWORD_LENGTH,
  isCommonPassword,
  passwordPolicyChecks,
  passwordPolicyError
} from '@/lib/password'

// The password policy is implemented twice - here and in the backend's
// UserManager.validate_password - so the expectations live in one file that
// both suites read. The backend copy is
// server/backend/tests/unit/api/users/test_password_policy.py.
const CASES_FILE = join(
  import.meta.dirname,
  '..',
  '..',
  '..',
  '..',
  'backend',
  'tests',
  'data',
  'password_policy_cases.json'
)
const { cases } = JSON.parse(readFileSync(CASES_FILE, 'utf8'))

describe('passwordPolicyError', () => {
  it('has a shared case table to run against', () => {
    // Guards the loop below: an unreadable or emptied table would make every
    // assertion vacuous in both languages at once.
    expect(cases.length).toBeGreaterThanOrEqual(10)
  })

  cases.forEach((testCase) => {
    it(`${testCase.name}`, () => {
      const error = passwordPolicyError(testCase.password, {
        email: testCase.email,
        username: testCase.username
      })
      expect(error).toBe(testCase.error)
    })
  })
})

describe('passwordPolicyChecks', () => {
  it('reports every rule, in the order the backend applies them', () => {
    const ids = passwordPolicyChecks('anything at all').map((check) => check.id)
    expect(ids).toEqual(['length', 'common', 'email', 'username'])
  })

  it('marks a compliant password as satisfying every rule', () => {
    const checks = passwordPolicyChecks('sixteen tonnes of quartz', {
      email: 'chemist@example.com',
      username: 'chemist'
    })
    expect(checks.every((check) => check.ok)).toBe(true)
  })

  it('reports the failing rules on a short common password', () => {
    const failed = passwordPolicyChecks('qwerty12').filter((check) => !check.ok)
    expect(failed.map((check) => check.id)).toEqual(['length'])
  })

  it('first failing check matches what passwordPolicyError returns', () => {
    const password = 'my chemist rocks'
    const identifiers = { email: 'chemist@example.com', username: 'labuser' }
    const firstFailure = passwordPolicyChecks(password, identifiers).find((check) => !check.ok)
    expect(passwordPolicyError(password, identifiers)).toBe(firstFailure.error)
  })

  it('treats an empty password as failing only the length rule', () => {
    const failed = passwordPolicyChecks('').filter((check) => !check.ok)
    expect(failed.map((check) => check.id)).toEqual(['length'])
  })
})

describe('isCommonPassword', () => {
  it('matches regardless of case', () => {
    expect(isCommonPassword('qwerty123456')).toBe(true)
    expect(isCommonPassword('QWERTY123456')).toBe(true)
  })

  it('does not flag a genuine passphrase', () => {
    expect(isCommonPassword('sixteen tonnes of quartz')).toBe(false)
  })

  it('tolerates null and undefined', () => {
    expect(isCommonPassword(null)).toBe(false)
    expect(isCommonPassword(undefined)).toBe(false)
  })

  it('flags the generated patterns, not just leaked entries', () => {
    // These are the shapes someone reaches for when told to make a password
    // longer, and a frequency list would only contain them by accident.
    expect(isCommonPassword('a'.repeat(MIN_PASSWORD_LENGTH))).toBe(true)
    expect(isCommonPassword('mascopemascope')).toBe(true)
  })
})
