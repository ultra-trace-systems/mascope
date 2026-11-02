import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { TURNSTILE_SCRIPT_URL } from '@/lib/turnstile'

// The app's Content-Security-Policy lets the sign-in form load Cloudflare's
// Turnstile script and frame, from Cloudflare's host alone. Without both, a
// site that turns Turnstile on gets a sign-in form whose check never loads, so
// nobody can sign in. The docs location keeps its own policy without them.

const FRONTEND_DIR = join(import.meta.dirname, '..', '..')
const TURNSTILE_ORIGIN = new URL(TURNSTILE_SCRIPT_URL).origin

const policies = (name) =>
  [
    ...readFileSync(join(FRONTEND_DIR, name), 'utf8').matchAll(/Content-Security-Policy "([^"]+)"/g)
  ].map((match) =>
    Object.fromEntries(
      match[1].split(';').map((part) => {
        const [directive, ...sources] = part.trim().split(/\s+/)
        return [directive, sources]
      })
    )
  )

describe.each(['nginx.conf', 'nginx.http.conf'])('%s', (name) => {
  it("allows Turnstile's script and frame in the app's policy", () => {
    const [app] = policies(name)
    expect(app['script-src']).toContain(TURNSTILE_ORIGIN)
    expect(app['frame-src']).toEqual([TURNSTILE_ORIGIN])
  })

  it("leaves the docs' policy without them", () => {
    const [, docs] = policies(name)
    expect(docs['script-src']).not.toContain(TURNSTILE_ORIGIN)
    expect(docs['frame-src']).toBeUndefined()
  })
})
