import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// An upload's acquisition record travels in one header line of the request
// that creates the upload: up to 16 KB of JSON, base64 in Upload-Metadata.
// nginx refuses a header line over 8 KB by default, with a 400, so both
// configs raise the limit. nginx-config-drift.spec.js keeps the two alike,
// which a line deleted from both satisfies; this holds that it is there, in
// each, and large enough.

const FRONTEND_DIR = join(import.meta.dirname, '..', '..')
const MAX_RECORD_BYTES = 16 * 1024
const UNITS = { '': 1, k: 1024, m: 1024 * 1024 }

function headerLineLimit(name) {
  const config = readFileSync(join(FRONTEND_DIR, name), 'utf8')
  const directive = config.match(/^\s*large_client_header_buffers\s+(\d+)\s+(\d+)([km]?)\s*;/im)
  if (!directive) return null
  return Number(directive[2]) * UNITS[directive[3].toLowerCase()]
}

describe('the header line an upload may send', () => {
  it.each(['nginx.conf', 'nginx.http.conf'])(
    '%s has room for a whole acquisition record',
    (name) => {
      const limit = headerLineLimit(name)
      // The record in base64, with the upload's other metadata beside it.
      const needed = Math.ceil(MAX_RECORD_BYTES / 3) * 4 + 2048

      expect(limit).not.toBeNull()
      expect(limit).toBeGreaterThanOrEqual(needed)
    }
  )
})
