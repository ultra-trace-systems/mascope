import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
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

// A copied batch or copied/cut samples belong further down than a workspace, so
// right-clicking the workspace's empty space offers a new dataset for them. On
// a dataset row - or the dataset in the breadcrumb, which opens this same menu
// on the focused dataset - the dataset's own actions are what is offered.
describe('dataset context menu: paste into a new dataset', () => {
  const SAMPLES = [{ sample_item_id: 's1', sample_batch_id: 'b1', sample_item_name: 'Blank' }]
  const BATCH = { sample_batch_id: 'b1', sample_batch_name: 'Morning QC' }
  const originalExecCommand = document.execCommand
  const entry = (menu, label) => menu.entries.find((e) => e.label === label)

  beforeEach(() => {
    vi.stubGlobal('navigator', {})
    document.execCommand = vi.fn(() => true)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    document.execCommand = originalExecCommand
    app.data.workspace.focused.is_system = false
  })

  const clipboard = async () =>
    (await import('@/lib/panes/PaneBrowserSample/stores/clipboard.js')).useClipboard()
  const paste = async () =>
    (await import('@/lib/panes/PaneBrowserSample/stores/pasteIntoNew.js')).usePasteIntoNew()

  it('offers copied samples a new dataset and batch on empty space', async () => {
    await (await clipboard()).copy(SAMPLES)
    const open = vi.spyOn(await paste(), 'open')
    const menu = useDatasetContextMenu()
    await menu.onClick({ data: null })

    const item = entry(menu, 'Paste sample into a new dataset and batch')
    expect(item.visible).toBe(true)
    expect(entry(menu, 'Paste batch into a new dataset').visible).toBe(false)
    item.command()
    expect(open).toHaveBeenCalledWith({ dataset: true })
  })

  it('offers a copied batch a new dataset on empty space', async () => {
    await (await clipboard()).copy(BATCH)
    const menu = useDatasetContextMenu()
    await menu.onClick({ data: null })

    expect(entry(menu, 'Paste batch into a new dataset').visible).toBe(true)
  })

  it('does not offer a new dataset on a dataset or in the system workspace', async () => {
    await (await clipboard()).copy(BATCH)
    const menu = useDatasetContextMenu()
    await menu.onClick({ data: DATASET })
    expect(entry(menu, 'Paste batch into a new dataset').visible).toBe(false)

    app.data.workspace.focused.is_system = true
    await menu.onClick({ data: null })
    expect(entry(menu, 'Paste batch into a new dataset').visible).toBe(false)
  })
})
