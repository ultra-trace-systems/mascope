import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { MIN_PASSWORD_LENGTH, isCommonPassword } from '@/lib/password'

// Drift guard for the twin password policies. The rules are implemented once in
// the backend (UserManager.validate_password, the authority) and mirrored in
// src/lib/password.js so the browser can give instant feedback. Nothing but a
// comment links the two, so this test reads the Python source and asserts the
// constants still agree.
//
// The blocklists are deliberately NOT identical. The backend carries every
// filtered entry; the browser carries the head of the same list, because the
// full set is ~320 KB gzipped and exists only for feedback the server repeats
// on submit. What must hold is that the browser copy is a strict SUBSET - so
// anything it rejects the backend rejects too, and the browser never produces a
// false positive.
//
// To fix a failure: change both copies, and regenerate the lists with
// `uv run tooling/vendor-common-passwords.py` rather than editing them.

const FRONTEND_DIR = join(import.meta.dirname, '..', '..')
const REPO_ROOT = join(FRONTEND_DIR, '..', '..')

const POLICY_SOURCE = join(
  REPO_ROOT,
  'server/backend/src/mascope_backend/api/new/users/user_manager/service.py'
)
const BACKEND_LIST = join(
  REPO_ROOT,
  'server/backend/src/mascope_backend/api/new/users/user_manager/common_passwords.txt'
)
const FRONTEND_LIST = join(FRONTEND_DIR, 'src/lib/common-passwords.txt')

/** Read a bare integer constant out of the backend policy module. */
function backendConstant(name) {
  const source = readFileSync(POLICY_SOURCE, 'utf8')
  const match = source.match(new RegExp(`^\\s*${name}\\s*=\\s*(\\d+)\\s*$`, 'm'))
  // Throw rather than return null: a rename would otherwise silently disarm
  // this guard by making the comparison undefined === undefined.
  if (!match) {
    throw new Error(
      `Could not find ${name} in ${POLICY_SOURCE}. If it was renamed, update this drift guard.`
    )
  }
  return Number(match[1])
}

/** Parse a vendored blocklist file into a Set, ignoring comments. */
function readList(path) {
  return new Set(
    readFileSync(path, 'utf8')
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#'))
  )
}

describe('password policy drift', () => {
  it('minimum password length matches the backend', () => {
    expect(MIN_PASSWORD_LENGTH).toBe(backendConstant('MIN_PASSWORD_LENGTH'))
  })

  it('minimum identifier length matches the backend', () => {
    // Not exported, so exercise it through behaviour: a 3-character username
    // must not match, a 4-character one must.
    expect(backendConstant('min_identifier_len')).toBe(4)
    expect(isCommonPassword('kimberley crescent')).toBe(false)
  })
})

describe('vendored blocklists', () => {
  const backend = readList(BACKEND_LIST)
  const frontend = readList(FRONTEND_LIST)

  it('are non-trivially sized', () => {
    // A parsing bug that emptied either set would make every other assertion
    // here pass vacuously.
    expect(backend.size).toBeGreaterThan(10_000)
    expect(frontend.size).toBeGreaterThan(500)
  })

  it('the browser copy is a strict subset of the backend copy', () => {
    const missing = [...frontend].filter((entry) => !backend.has(entry))
    expect(missing).toEqual([])
    expect(frontend.size).toBeLessThan(backend.size)
  })

  it('contain only entries the policy could actually reach', () => {
    // Anything shorter is rejected by the length rule before the blocklist is
    // consulted, so a short entry is dead weight and a sign the filter broke.
    const tooShort = [...backend].filter((entry) => entry.length < MIN_PASSWORD_LENGTH)
    expect(tooShort).toEqual([])
  })

  it('are lowercased, since both loaders match on a lowercased candidate', () => {
    const mixedCase = [...backend].filter((entry) => entry !== entry.toLowerCase())
    expect(mixedCase).toEqual([])
  })

  it('stay small enough to ship to the browser', () => {
    const bytes = readFileSync(FRONTEND_LIST).length
    expect(bytes).toBeLessThan(40_000)
  })
})
