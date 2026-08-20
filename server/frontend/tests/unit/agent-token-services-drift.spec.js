import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// Drift guard for which services the settings pane offers a manual token for.
//
// Two registries have to stay in step. The backend decides which services can
// be PAIRED (pairing_settings.ALLOWED_SERVICES); the settings pane decides
// which ones a person can still mint a token for by hand. They are related but
// not identical, and the difference is the point:
//
//   - mascope_sdk cannot pair (interactive users copy their token), so it must
//     stay in the dropdown.
//   - file-agent can pair AND its shipped client only accepts a paired
//     credential, so a hand-minted token would be unusable - it is hidden.
//   - tof-agent and export-agent can pair server-side, but no pairing-capable
//     client exists for them, so the dropdown is their only way to get a
//     credential and they must stay until that changes.
//
// The pane is a .vue SFC, so this reads its source rather than mounting it.

const FRONTEND_DIR = join(import.meta.dirname, '..', '..')
const REPO_ROOT = join(FRONTEND_DIR, '..', '..')

const PANE = join(
  FRONTEND_DIR,
  'src/lib/toolbars/ToolbarAppFilters/SidebarMenu/UserSettingsPane.vue'
)
const PAIRING_CONFIG = join(
  REPO_ROOT,
  'server/backend/src/mascope_backend/api/new/auth/pairing/config.py'
)

/** The service ids in SERVICE_CONFIGS, and whether each is marked pairedOnly. */
const readServiceConfigs = () => {
  const source = readFileSync(PANE, 'utf8')
  const block = source.slice(
    source.indexOf('const SERVICE_CONFIGS'),
    source.indexOf('const selectedTokenType')
  )
  return block
    .split(/\{\s*\n/)
    .slice(1)
    .map((entry) => ({
      id: entry.match(/id:\s*'([^']+)'/)?.[1],
      pairedOnly: /pairedOnly:\s*true/.test(entry)
    }))
    .filter((entry) => entry.id)
}

describe('agent token services', () => {
  it('hides only file-agent from manual token generation', () => {
    const configs = readServiceConfigs()
    const hidden = configs.filter((c) => c.pairedOnly).map((c) => c.id)
    const offered = configs.filter((c) => !c.pairedOnly).map((c) => c.id)

    expect(hidden).toEqual(['file-agent'])
    expect(offered).toContain('mascope_sdk')
    // Their clients cannot pair; removing them would leave no way to issue one.
    expect(offered).toContain('tof-agent')
    expect(offered).toContain('export-agent')
  })

  it('keeps a label for every service that can be paired', () => {
    // Paired machines rows render through SERVICE_CONFIGS, so a pairable
    // service missing from it would show a raw id like "file-agent".
    const allowed = readFileSync(PAIRING_CONFIG, 'utf8')
      .match(/ALLOWED_SERVICES:[^=]*=\s*\(([^)]*)\)/s)?.[1]
      .match(/'([^']+)'|"([^"]+)"/g)
      .map((s) => s.replace(/['"]/g, ''))

    const known = readServiceConfigs().map((c) => c.id)
    expect(allowed.length).toBeGreaterThan(0)
    for (const service of allowed) {
      expect(known).toContain(service)
    }
  })
})
