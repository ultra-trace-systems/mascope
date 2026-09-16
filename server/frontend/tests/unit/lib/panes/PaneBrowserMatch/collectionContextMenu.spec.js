import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// Right-clicking the target browser away from a collection offers to edit the
// open batch's targets. With no batch open there is no batch to edit, so there
// must be no such entry and no menu at all.

const app = {
  auth: { user: { role_id: 1 } },
  data: {
    batch: { focused: null },
    target: { collection: { loadDetailed: vi.fn() } }
  }
}

vi.mock('@/stores', () => ({ useApp: () => app }))

const BATCH = { sample_batch_id: 'b1', sample_batch_name: 'Morning QC' }

let menu
const popup = { toggle: vi.fn(), hide: vi.fn() }

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  app.data.batch.focused = null
  const { useCollectionContextMenu } =
    await import('@/lib/panes/PaneBrowserMatch/stores/collectionContextMenu.js')
  menu = useCollectionContextMenu()
  menu.ref = popup
})

const labels = () => menu.entries.map(({ label }) => label)

describe('collection context menu away from a collection', () => {
  it('offers to edit the open batch targets', async () => {
    app.data.batch.focused = BATCH
    await menu.onClick(new MouseEvent('contextmenu'))

    expect(labels()).toEqual(['Edit batch targets'])
    expect(popup.toggle).toHaveBeenCalled()
  })

  it('opens no menu when no batch is open', async () => {
    await menu.onClick(new MouseEvent('contextmenu'))

    expect(labels()).toEqual([])
    expect(popup.toggle).not.toHaveBeenCalled()
  })

  it('still offers the collection entries on a collection row', async () => {
    await menu.onClick({
      data: { target_collection_id: 'c1', target_collection_name: 'VOCs', workspace_id: 'w1' },
      originalEvent: new MouseEvent('contextmenu')
    })

    expect(labels()).not.toContain('Edit batch targets')
    expect(labels()).toContain("Edit 'VOCs'")
    expect(popup.toggle).toHaveBeenCalled()
  })
})
