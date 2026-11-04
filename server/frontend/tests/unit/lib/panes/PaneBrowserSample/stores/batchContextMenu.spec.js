import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { reactive } from 'vue'

// Cut batch and Paste batch move a batch into another dataset. The menu offers
// the cut only where the move route would take it - not an ACQUISITION batch,
// not without the editor role, a locked batch or dataset only for a global
// admin - and the paste only into another dataset the user may edit, never a
// locked or ACQUISITION one. A pasted cut is dropped from the clipboard; a
// refused one stays, so it can be pasted elsewhere.

const app = reactive({
  auth: { user: null },
  data: {
    workspace: { focusedId: 'ws-1', focused: null, list: [] },
    dataset: { focusedId: 'ds-1', focused: null },
    batch: { copy: vi.fn(), move: vi.fn(), focused: null },
    sample: { list: [], copy: vi.fn(), move: vi.fn() }
  }
})

vi.mock('@/stores', () => ({ useApp: () => app }))
vi.mock('primevue/useconfirm', () => ({ useConfirm: () => ({ require: vi.fn() }) }))
vi.mock('@/lib/dialogs', () => ({ useBatchDeleteDialog: () => vi.fn() }))
vi.mock('@/lib/panes/PaneBrowserSample/stores/sampleContextMenu.js', () => ({
  useSampleContextMenu: () => ({ hide: vi.fn() })
}))
vi.mock('@/lib/panes/PaneBrowserSample/stores/customizerPopover.js', () => ({
  useCustomizerPopover: () => ({ hide: vi.fn() })
}))

const BATCH = {
  sample_batch_id: 'b-1',
  sample_batch_name: 'Morning QC',
  sample_batch_description: '',
  sample_batch_type: 'MEASUREMENT',
  dataset_id: 'ds-1',
  locked: 0
}
const EDITOR = { role_id: 200, is_superuser: false }
const ADMIN = { role_id: 300, is_superuser: false }

let menu
let clipboard
const originalExecCommand = document.execCommand

const entry = (label) => menu.entries.find((e) => e.label === label)

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  vi.stubGlobal('navigator', {})
  document.execCommand = vi.fn(() => true)
  app.auth.user = EDITOR
  app.data.workspace.focused = { workspace_id: 'ws-1', is_system: false, my_role: 'editor' }
  app.data.dataset.focusedId = 'ds-1'
  app.data.dataset.focused = { dataset_id: 'ds-1', dataset_type: 'STANDARD', locked: 0 }
  const { useBatchContextMenu } =
    await import('@/lib/panes/PaneBrowserSample/stores/batchContextMenu.js')
  const { useClipboard } = await import('@/lib/panes/PaneBrowserSample/stores/clipboard.js')
  menu = useBatchContextMenu()
  clipboard = useClipboard()
})

afterEach(() => {
  vi.unstubAllGlobals()
  document.execCommand = originalExecCommand
})

// Cut the batch on its row, then open the other dataset's empty space.
async function cutAndOpen(target = { dataset_id: 'ds-2', dataset_type: 'STANDARD', locked: 0 }) {
  await menu.onClick({ data: BATCH })
  entry('Cut batch').command()
  await vi.waitFor(() => expect(clipboard.op).toBe('cut'))
  app.data.dataset.focusedId = target.dataset_id
  app.data.dataset.focused = target
  await menu.onClick({ data: null })
}

describe('batch context menu: cut batch', () => {
  it('is offered on an editable batch', async () => {
    await menu.onClick({ data: BATCH })
    expect(entry('Cut batch').visible).toBe(true)
  })

  it('is not offered on an acquisition batch', async () => {
    await menu.onClick({ data: { ...BATCH, sample_batch_type: 'ACQUISITION' } })
    expect(entry('Cut batch').visible).toBe(false)
  })

  it('is not offered to a guest of the workspace', async () => {
    app.data.workspace.focused.my_role = 'guest'
    await menu.onClick({ data: BATCH })
    expect(entry('Cut batch').visible).toBe(false)
  })

  it('is offered on a locked batch or dataset only to a global admin', async () => {
    await menu.onClick({ data: { ...BATCH, locked: 1 } })
    expect(entry('Cut batch').visible).toBe(false)

    await menu.onClick({ data: BATCH })
    app.data.dataset.focused.locked = 1
    expect(entry('Cut batch').visible).toBe(false)

    app.auth.user = ADMIN
    expect(entry('Cut batch').visible).toBe(true)
    await menu.onClick({ data: { ...BATCH, locked: 1 } })
    expect(entry('Cut batch').visible).toBe(true)
  })
})

describe('batch context menu: paste a cut batch', () => {
  it('moves the batch into the focused dataset and drops the cut', async () => {
    await cutAndOpen()
    expect(entry('Paste batch').visible).toBe(true)

    await entry('Paste batch').command()

    expect(app.data.batch.move).toHaveBeenCalledWith({ sample_batch_id: 'b-1', dataset_id: 'ds-2' })
    expect(app.data.batch.copy).not.toHaveBeenCalled()
    expect(clipboard.batch).toBeNull()
  })

  it('keeps the cut when the move is refused', async () => {
    // The request layer shows the route's refusal as a toast and rethrows.
    app.data.batch.move.mockRejectedValueOnce(new Error('403'))
    await cutAndOpen()

    await expect(entry('Paste batch').command()).rejects.toThrow('403')
    expect(clipboard.batch?.sample_batch_id).toBe('b-1')
  })

  it('is not offered back into the dataset it came from', async () => {
    const show = vi.fn()
    menu.ref = { show, hide: vi.fn() }
    await cutAndOpen({ dataset_id: 'ds-1', dataset_type: 'STANDARD', locked: 0 })
    expect(entry('Paste batch').visible).toBe(false)
    // nothing to offer on the empty space, so no empty menu opens
    expect(show).toHaveBeenCalledTimes(1)
  })

  it('is not offered on a batch row', async () => {
    await cutAndOpen()
    await menu.onClick({ data: { ...BATCH, sample_batch_id: 'b-2', dataset_id: 'ds-2' } })
    expect(entry('Paste batch').visible).toBe(false)
  })

  it('is not offered into a locked or acquisition dataset', async () => {
    await cutAndOpen({ dataset_id: 'ds-2', dataset_type: 'STANDARD', locked: 1 })
    expect(entry('Paste batch').visible).toBe(false)

    app.data.dataset.focused = { dataset_id: 'ds-2', dataset_type: 'ACQUISITION', locked: 0 }
    expect(entry('Paste batch').visible).toBe(false)
  })

  it('is not offered into a dataset of a workspace the user only reads', async () => {
    await cutAndOpen()
    app.data.workspace.focused = { workspace_id: 'ws-2', is_system: false, my_role: 'guest' }
    expect(entry('Paste batch').visible).toBe(false)
  })

  it('is not offered into a system workspace', async () => {
    await cutAndOpen()
    app.data.workspace.focused = { workspace_id: 'ws-sys', is_system: true, my_role: 'owner' }
    expect(entry('Paste batch').visible).toBe(false)
  })
})

describe('batch context menu: paste a copied batch', () => {
  it('still copies, wherever it is pasted', async () => {
    await menu.onClick({ data: BATCH })
    entry('Copy batch').command()
    await vi.waitFor(() => expect(clipboard.op).toBe('copy'))
    await menu.onClick({ data: null })

    expect(entry('Paste batch').visible).toBe(true)
    await entry('Paste batch').command()
    expect(app.data.batch.copy).toHaveBeenCalled()
    expect(app.data.batch.move).not.toHaveBeenCalled()
  })
})
