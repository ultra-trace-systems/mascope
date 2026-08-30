import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// "Copy assignments to batch..." is the UI entry point for the copy feature.
// It publishes a run onto every eligible sample of the batch, so it must open
// the confirmation dialog rather than start anything on the click - and it
// must be gated with the rest of the peak-assignment surfaces, since without
// the feature there is nowhere to view what it would produce.

// The menu store's dependency graph reaches modules that read the injected
// runtime config at import time (the api client, the feature flags). Provide it
// the way the container does, rather than stubbing every one of them.
window.__MASCOPE_RUNTIME__ = { meta: { api_port: 8090, peak_assignment: true }, mode: 'test' }

const app = {
  data: {
    sample: {
      selected: [],
      selectedIds: [],
      isSelected: () => true,
      focus: vi.fn(),
      focused: null
    },
    batch: { focused: { status: 'ready' }, focusedId: 'sb-1' },
    workspace: { list: [], focusedId: 'ws-1' }
  },
  auth: { user: { id: 1 } }
}

vi.mock('@/stores', () => ({ useApp: () => app }))
vi.mock('@/api', () => ({ api: { http: { get: vi.fn(), post: vi.fn() } } }))
vi.mock('primevue/useconfirm', () => ({ useConfirm: () => ({ require: vi.fn() }) }))

// Compiling this store's dependency graph (the sibling menus, the dialogs they
// mount, PrimeVue) is the expensive part, and as a dynamic import inside the
// first test it lands on that one test's clock and trips the timeout. Imported
// once here - statically, so the mocks above still apply - which also warms the
// transform cache for the per-test re-imports below.
await import('@/lib/panes/PaneBrowserSample/stores/sampleContextMenu.js')

const SAMPLE = {
  sample_item_id: 'si-1',
  sample_item_name: 'Curated Sample',
  sample_batch_id: 'sb-1',
  instrument: 'orbi'
}

const copyEntry = (menu) =>
  menu.entries
    .find(({ label }) => label === 'Process')
    ?.items?.find(({ label }) => label === 'Copy assignments to batch...')

// The right-clicked row is set directly rather than through `onClick`, which
// additionally reads the system clipboard for its paste entry - not something
// this entry depends on.
async function openMenu({ enabled = true, selected = [SAMPLE] } = {}) {
  vi.doMock('@/lib/features', () => ({ peakAssignmentEnabled: enabled }))
  const { useSampleContextMenu } =
    await import('@/lib/panes/PaneBrowserSample/stores/sampleContextMenu.js')
  app.data.sample.selected = selected
  const menu = useSampleContextMenu()
  menu.row = SAMPLE
  return menu
}

describe('sample context menu: copy assignments to batch', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.resetModules()
  })

  it('opens the confirmation dialog instead of starting the copy', async () => {
    const menu = await openMenu()

    expect(menu.dialog.copyAssignments).toBe(false)
    copyEntry(menu).command()

    // A fan-out over the whole batch is too much to commit to on one click.
    expect(menu.dialog.copyAssignments).toBe(true)
  })

  it('offers the entry for the right-clicked sample', async () => {
    const menu = await openMenu()

    expect(copyEntry(menu).visible).toBe(true)
    expect(menu.row).toEqual(SAMPLE)
  })

  it('is hidden when peak assignment is disabled for this deployment', async () => {
    const menu = await openMenu({ enabled: false })

    expect(copyEntry(menu).visible).toBe(false)
  })

  it('is hidden while several samples are selected', async () => {
    // The copy is defined by ONE curated source run; offering it over a
    // multi-selection would not say which sample it copies from.
    const menu = await openMenu({ selected: [SAMPLE, { sample_item_id: 'si-2' }] })

    expect(copyEntry(menu).visible).toBe(false)
  })
})
