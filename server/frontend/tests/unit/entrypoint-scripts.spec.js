import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// docker-entrypoint.sh runs helper scripts by absolute path (`sh /name.sh`),
// and runs under `set -e`: a helper the image does not carry stops the
// frontend at start. Nothing in CI starts the built image, so check here that
// the Dockerfile copies each one to the path the entrypoint runs it from.

const FRONTEND_DIR = join(import.meta.dirname, '..', '..')
const read = (name) => readFileSync(join(FRONTEND_DIR, name), 'utf8')

describe('docker-entrypoint.sh helpers', () => {
  it('are each copied into the image where the entrypoint runs them', () => {
    const helpers = [...read('docker-entrypoint.sh').matchAll(/^\s*sh (\/[\w.-]+\.sh)\s*$/gm)].map(
      (m) => m[1],
    )
    expect(helpers).toEqual(['/realip-gateway.sh', '/origin-pull.sh'])

    const dockerfile = read('Dockerfile')
    for (const path of helpers) {
      expect(dockerfile, `the Dockerfile does not copy ${path}`).toContain(
        `COPY server/frontend${path} ${path}\n`,
      )
    }
  })
})
