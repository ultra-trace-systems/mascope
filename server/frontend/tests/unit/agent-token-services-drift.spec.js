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
//   - ai-agent is a person's own token for an AI agent working through the
//     SDK; it does not pair either, so the dropdown is where it is minted.
//   - file-agent can pair AND its shipped client only accepts a paired
//     credential, so a hand-minted token would be unusable - it is hidden.
//   - instrument-agent is that same client inside an instrument control
//     program, pairing under a name of its own - hidden for the same reason.
//   - tof-agent and export-agent can pair server-side, but no pairing-capable
//     client exists for them, so the dropdown is their only way to get a
//     credential and they must stay until that changes.
//
// Both the pane and the pairing dialog label every pairable service. They are
// .vue SFCs, so this reads their source rather than mounting them.

const FRONTEND_DIR = join(import.meta.dirname, '..', '..')
const REPO_ROOT = join(FRONTEND_DIR, '..', '..')

const PANE = join(
  FRONTEND_DIR,
  'src/lib/toolbars/ToolbarAppFilters/SidebarMenu/UserSettingsPane.vue'
)
const DIALOG = join(FRONTEND_DIR, 'src/lib/dialogs/DialogAgentPairing.vue')
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

/** The service ids the pairing dialog has a label for. */
const readDialogLabels = () => {
  const source = readFileSync(DIALOG, 'utf8')
  const block = source.match(/const SERVICE_LABELS = \{([^}]*)\}/)?.[1] ?? ''
  return [...block.matchAll(/'([^']+)':/g)].map((m) => m[1])
}

/** The services the backend lets pair, read from pairing/config.py. */
const readPairableServices = () =>
  readFileSync(PAIRING_CONFIG, 'utf8')
    .match(/ALLOWED_SERVICES:[^=]*=\s*\(([^)]*)\)/s)?.[1]
    .match(/'([^']+)'|"([^"]+)"/g)
    .map((s) => s.replace(/['"]/g, ''))

describe('agent token services', () => {
  it('hides only the agents that pair from manual token generation', () => {
    const configs = readServiceConfigs()
    const hidden = configs.filter((c) => c.pairedOnly).map((c) => c.id)
    const offered = configs.filter((c) => !c.pairedOnly).map((c) => c.id)

    expect(hidden).toEqual(['file-agent', 'instrument-agent'])
    expect(offered).toContain('mascope_sdk')
    expect(offered).toContain('ai-agent')
    // Their clients cannot pair; removing them would leave no way to issue one.
    expect(offered).toContain('tof-agent')
    expect(offered).toContain('export-agent')
  })

  it('keeps a label for every service that can be paired', () => {
    // Paired machines rows render through SERVICE_CONFIGS, so a pairable
    // service missing from it would show a raw id like "file-agent".
    const allowed = readPairableServices()

    const known = readServiceConfigs().map((c) => c.id)
    expect(allowed.length).toBeGreaterThan(0)
    for (const service of allowed) {
      expect(known).toContain(service)
    }
  })

  it('names every service that can be paired when a pairing is approved', () => {
    // The approval message falls back to the raw id for a service it has no
    // label for, as the Paired machines rows do.
    const allowed = readPairableServices()

    const labelled = readDialogLabels()
    expect(allowed).toContain('instrument-agent')
    for (const service of allowed) {
      expect(labelled).toContain(service)
    }
  })
})
