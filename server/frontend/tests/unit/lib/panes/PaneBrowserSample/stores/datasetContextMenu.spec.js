import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// "Refresh matches" on a dataset is the one entry here that starts work rather
// than opening a dialog, and it acts on the RIGHT-CLICKED dataset - which is
// not necessarily the focused one, since right-clicking a row does not select
// it. It also runs over every batch in the dataset and cannot be stopped, so
// it has to go through the confirmation before anything is posted.

const rematch = vi.fn()
const confirmRequire = vi.fn()

const app = {
  data: {
    dataset: { rematch, move: vi.fn(), focusedId: 'ds-focused' },
    workspace: { focusedId: 'ws-1', focused: { workspace_id: 'ws-1', is_system: false } }
  }
}

vi.mock('@/stores', () => ({ useApp: () => app }))
vi.mock('primevue/useconfirm', () => ({ useConfirm: () => ({ require: confirmRequire }) }))

const DATASET = { dataset_id: 'ds-2', dataset_name: 'Campaign 2024', workspace_id: 'ws-1' }

let useDatasetContextMenu

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  ;({ useDatasetContextMenu } =
    await import('@/lib/panes/PaneBrowserSample/stores/datasetContextMenu.js'))
})

const refreshEntry = (menu) =>
  menu.entries
    .find(({ label }) => label === 'Process')
    ?.items?.find(({ label }) => label === 'Refresh matches')

describe('dataset context menu: refresh matches', () => {
  it('refreshes the right-clicked dataset, not the focused one', async () => {
    const menu = useDatasetContextMenu()
    await menu.onClick({ data: DATASET })

    refreshEntry(menu).command()

    // Nothing is posted until the confirmation is accepted.
    expect(rematch).not.toHaveBeenCalled()
    expect(confirmRequire).toHaveBeenCalledTimes(1)
    const { message, accept } = confirmRequire.mock.calls[0][0]
    expect(message).toContain(DATASET.dataset_name)

    accept()
    expect(rematch).toHaveBeenCalledWith({ dataset_id: 'ds-2' })
  })

  it('still refreshes the confirmed dataset after the menu has moved on', async () => {
    // The confirmation outlives the menu: right-clicking elsewhere while the
    // dialog is open clears the menu's row, so accepting has to act on the
    // dataset that was confirmed rather than on whatever the row holds then.
    const menu = useDatasetContextMenu()
    await menu.onClick({ data: DATASET })

    refreshEntry(menu).command()
    const { accept } = confirmRequire.mock.calls[0][0]
    menu.hide()

    accept()
    expect(rematch).toHaveBeenCalledWith({ dataset_id: 'ds-2' })
  })

  it('offers no dataset actions when opened on empty space', async () => {
    const menu = useDatasetContextMenu()
    await menu.onClick({ data: null })

    expect(menu.entries.find(({ label }) => label === 'Process').visible).toBe(false)
  })
})
