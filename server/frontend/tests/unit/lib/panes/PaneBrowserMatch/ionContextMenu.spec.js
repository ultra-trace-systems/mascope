import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// "Open match" is what enables the Match tab: it visualizes the ion's match in
// the focused sample and brings the tab forward. Selecting a row does not, since
// the selection drives the Batch chart. A match is per sample, so the entry is
// only offered with a sample focused.

const app = {
  data: {
    sample: { focusedId: null },
    target: { collection: { detailed: null } },
    match: {
      collection: { focusedId: 'tc-1' },
      visualized: { ion: null, set: vi.fn() }
    }
  },
  ui: { tab: { active: 'batch' } }
}

vi.mock('@/stores', () => ({ useApp: () => app }))

const ION = {
  target_ion_id: 'ti-1',
  target_compound_id: 'tco-1',
  target_compound_name: 'Toluene'
}

let menu

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  app.data.sample.focusedId = null
  app.data.match.visualized.ion = null
  // Debounced in the real store: it returns at once, before anything loads.
  app.data.match.visualized.set.mockReturnValue(undefined)
  app.ui.tab.active = 'batch'
  const { useIonContextMenu } =
    await import('@/lib/panes/PaneBrowserMatch/stores/ionContextMenu.js')
  menu = useIonContextMenu()
  menu.selection = ION
})

const labels = () => menu.entries.filter((entry) => !entry.separator).map(({ label }) => label)
const openMatch = () => menu.entries.find(({ label }) => label === 'Open match')

describe('the ion context menu', () => {
  it('offers "Open match" first when a sample is focused', () => {
    app.data.sample.focusedId = 'si-1'

    expect(labels()[0]).toBe('Open match')
  })

  it('offers no "Open match" without a focused sample', () => {
    expect(labels()).not.toContain('Open match')
    expect(labels()).toContain("Edit compound 'Toluene'")
  })

  it('opens the match of the right-clicked ion in the focused sample', async () => {
    app.data.sample.focusedId = 'si-1'

    await openMatch().command()

    expect(app.data.match.visualized.set).toHaveBeenCalledWith({
      sampleId: 'si-1',
      ionId: 'ti-1',
      collectionId: 'tc-1'
    })
  })

  // The tab store follows a new match everywhere but out of the Sample tab;
  // an explicit "Open match" goes there from the Sample tab too. Without
  // waiting for the load: `set` is debounced, and nothing could be awaited.
  it.each(['batch', 'sample'])('activates the Match tab from the %s tab', async (from) => {
    app.data.sample.focusedId = 'si-1'
    app.ui.tab.active = from

    openMatch().command()

    expect(app.ui.tab.active).toBe('match')
  })

  it('opens nothing without a focused sample', async () => {
    await menu.openMatch(ION)

    expect(app.data.match.visualized.set).not.toHaveBeenCalled()
    expect(app.ui.tab.active).toBe('batch')
  })
})
