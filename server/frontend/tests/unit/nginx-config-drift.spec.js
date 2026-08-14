import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// Drift guard for the twin nginx configs. nginx.conf (HTTPS/prod) and
// nginx.http.conf (demo/localhost) are baked into the same frontend image;
// docker-entrypoint.sh selects one at container start based on MASCOPE_TLS. The
// two share a host-agnostic body (caching, upstream, security headers, gzip,
// forwarded headers, the SPA/docs/api/upload locations) and legitimately differ
// only in host-specific parts (TLS, the Cloudflare realip trust list, the
// rate-limit apparatus, HSTS, per-location timeouts).
//
// Each host-specific run is wrapped in its own file as:
//   # >>> host-only:NAME
//   ...directives that belong to this host only...
//   # <<< host-only:NAME
// Everything outside those markers must be the same directive lines in the same
// order across the two files. This test strips comments (whole-line and inline),
// blank lines, and host-only regions from each config, then compares the
// remaining directive lines in order. A host-agnostic directive added to, edited
// in, removed from, or moved to a different block in only one file diverges here.
// Leading/trailing whitespace and comment text are ignored; a directive's own
// text (including internal spacing) must match, so a value or formatting change
// on one side is caught.
//
// To fix a failure: mirror the change into both files, or -- if the directive is
// genuinely host-specific -- wrap it in a `# >>> host-only:NAME` marker.

const FRONTEND_DIR = join(import.meta.dirname, '..', '..')
const configPath = (name) => join(FRONTEND_DIR, name)

const OPEN = /^#\s*>>>\s*host-only:(\S+)\s*$/
const CLOSE = /^#\s*<<<\s*host-only:(\S+)\s*$/

// Drop an inline comment (the first '#' that is not inside a quoted string),
// then trim. A whole-line comment collapses to '', as does a blank line.
function cleanLine(raw) {
  let inSingle = false
  let inDouble = false
  let cut = -1
  for (let i = 0; i < raw.length; i++) {
    const c = raw[i]
    if (c === "'" && !inDouble) inSingle = !inSingle
    else if (c === '"' && !inSingle) inDouble = !inDouble
    else if (c === '#' && !inSingle && !inDouble) {
      cut = i
      break
    }
  }
  return (cut >= 0 ? raw.slice(0, cut) : raw).trim()
}

// Parse a config into its shared directive lines (comments, blanks, and host-only
// regions removed) and the host-only regions it declares (with the directive
// count each encloses). Throws on a malformed marker so a deleted, renamed,
// nested, or unbalanced marker cannot silently disarm the guard.
function parseConfig(path) {
  const lines = readFileSync(path, 'utf8').split(/\r?\n/)
  const directives = []
  const regions = []
  let open = null
  lines.forEach((raw, i) => {
    const trimmed = raw.trim()
    const at = `${path}:${i + 1}`

    const openMatch = trimmed.match(OPEN)
    if (openMatch) {
      const name = openMatch[1]
      if (open) throw new Error(`${at}: host-only:${name} opened inside host-only:${open} (no nesting)`)
      if (regions.some((r) => r.name === name)) throw new Error(`${at}: duplicate host-only region name "${name}"`)
      open = name
      regions.push({ name, lines: 0 })
      return
    }
    const closeMatch = trimmed.match(CLOSE)
    if (closeMatch) {
      const name = closeMatch[1]
      if (!open) throw new Error(`${at}: host-only:${name} closed without a matching open`)
      if (name !== open) throw new Error(`${at}: host-only:${name} closes while host-only:${open} is open`)
      open = null
      return
    }

    const cleaned = cleanLine(raw)
    if (open) {
      if (cleaned !== '') regions[regions.length - 1].lines++
      return
    }
    if (cleaned === '') return // comment or blank line
    directives.push(cleaned)
  })
  if (open) throw new Error(`${path}: host-only:${open} is never closed`)
  return { directives, regions }
}

// Human-readable divergences between the two shared bodies: content differences
// first (a line present a different number of times), then the first placement
// difference when the two carry the same lines in a different order.
function divergences(prod, demo) {
  const surplus = (a, b) => {
    const count = (lines) => lines.reduce((m, l) => m.set(l, (m.get(l) || 0) + 1), new Map())
    const ca = count(a)
    const cb = count(b)
    const out = []
    for (const [line, n] of ca) {
      for (let k = 0; k < n - (cb.get(line) || 0); k++) out.push(line)
    }
    return out
  }
  const problems = [
    ...surplus(prod, demo).map((l) => `only in nginx.conf:      ${l}`),
    ...surplus(demo, prod).map((l) => `only in nginx.http.conf: ${l}`),
  ]
  if (problems.length === 0) {
    const n = Math.min(prod.length, demo.length)
    for (let i = 0; i < n; i++) {
      if (prod[i] !== demo[i]) {
        problems.push(`directive #${i + 1} is placed differently: nginx.conf has "${prod[i]}", nginx.http.conf has "${demo[i]}"`)
        break
      }
    }
  }
  return problems
}

describe('nginx twin configs', () => {
  it('declare well-formed, non-empty host-only markers', () => {
    for (const name of ['nginx.conf', 'nginx.http.conf']) {
      const { regions } = parseConfig(configPath(name))
      expect(regions.length, `${name} declares no host-only regions`).toBeGreaterThan(0)
      const empty = regions.filter((r) => r.lines === 0).map((r) => r.name)
      expect(empty, `${name} has empty host-only regions`).toEqual([])
    }
  })

  it('share the same host-agnostic body, in the same order', () => {
    const prod = parseConfig(configPath('nginx.conf'))
    const demo = parseConfig(configPath('nginx.http.conf'))

    expect(divergences(prod.directives, demo.directives)).toEqual([])

    // Fail loudly if a parsing bug ever strips the whole body to nothing, which
    // would otherwise let the comparison above pass vacuously.
    expect(prod.directives.length).toBeGreaterThan(20)
  })
})
