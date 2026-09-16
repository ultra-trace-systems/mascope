import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { reactive, nextTick } from 'vue'

// Pasting a batch or samples one level above where they belong creates the
// missing dataset and batch first, then pastes into them exactly as a paste
// into existing ones would. A failure part way leaves the dialog open for a
// retry, which must not create the containers a second time.

const app = reactive({
  data: {
    workspace: { focusedId: 'ws-1', focused: { workspace_id: 'ws-1', is_system: false } },
    dataset: {
      focusedId: null,
      focused: null,
      list: [],
      create: vi.fn(),
      focus: vi.fn()
    },
    batch: { list: [], create: vi.fn(), copy: vi.fn(), focus: vi.fn() },
    sample: { copy: vi.fn(), move: vi.fn() }
  }
})

vi.mock('@/stores', () => ({ useApp: () => app }))

const BATCH = {
  sample_batch_id: 'b-src',
  sample_batch_name: 'Morning QC',
  sample_batch_description: 'QC runs'
}
const SAMPLES = [
  { sample_item_id: 's1', sample_batch_id: 'b-src', sample_item_name: 'Blank' },
  { sample_item_id: 's2', sample_batch_id: 'b-src', sample_item_name: 'Standard' }
]

let clipboard
let paste

function arriveLater(store, record) {
  setTimeout(() => {
    store.list = [...store.list, record]
  }, 10)
}
const arrived = async () => {
  await new Promise((resolve) => setTimeout(resolve, 20))
  await nextTick()
}
const originalExecCommand = document.execCommand

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.stubGlobal('navigator', {})
  document.execCommand = vi.fn(() => true)
  app.data.dataset.focusedId = null
  app.data.dataset.list = []
  app.data.batch.list = []
  app.data.workspace.focused.is_system = false
  // The created record reaches the list over the socket after the request
  // has returned.
  app.data.dataset.create.mockImplementation(async () => {
    arriveLater(app.data.dataset, { dataset_id: 'ds-new' })
    return { data: { dataset_id: 'ds-new' } }
  })
  app.data.batch.create.mockImplementation(async () => {
    arriveLater(app.data.batch, { sample_batch_id: 'b-new' })
    return { data: { sample_batch_id: 'b-new' } }
  })
  const { useClipboard } = await import('@/lib/panes/PaneBrowserSample/stores/clipboard.js')
  const { usePasteIntoNew } = await import('@/lib/panes/PaneBrowserSample/stores/pasteIntoNew.js')
  clipboard = useClipboard()
  paste = usePasteIntoNew()
})

afterEach(() => {
  vi.unstubAllGlobals()
  document.execCommand = originalExecCommand
})

describe('paste into a new dataset', () => {
  it('copies samples into a new dataset and batch and lands in the batch', async () => {
    await clipboard.copy(SAMPLES)
    expect(paste.datasetValid).toBe(true)

    paste.open({ dataset: true })
    expect(paste.dialog.batch).toBe(true)
    paste.dialog.datasetName = ' Campaign '
    // no name is filled in for the batch, and none is made up for it
    expect(paste.invalid).toBe(true)
    paste.dialog.batchName = ' Run 1 '
    expect(paste.invalid).toBe(false)
    await paste.execute()
    await arrived()

    expect(app.data.dataset.create).toHaveBeenCalledWith({
      dataset_name: 'Campaign',
      dataset_description: ''
    })
    expect(app.data.batch.create).toHaveBeenCalledWith(
      expect.objectContaining({ dataset_id: 'ds-new', sample_batch_name: 'Run 1' })
    )
    expect(app.data.sample.copy).toHaveBeenCalledWith({
      sample_item_ids: ['s1', 's2'],
      sample_batch_id: 'b-new'
    })
    expect(app.data.sample.move).not.toHaveBeenCalled()
    expect(app.data.dataset.focus).toHaveBeenCalledWith({ dataset_id: 'ds-new' })
    expect(app.data.batch.focus).toHaveBeenCalledWith({ sample_batch_id: 'b-new' })
    expect(paste.dialog.visible).toBe(false)
  })

  it('copies a batch into a new dataset the user names, keeping its own name', async () => {
    await clipboard.copy(BATCH)

    paste.open({ dataset: true })
    expect(paste.dialog.batch).toBe(false)
    // the dataset is not named after the batch
    expect(paste.dialog.datasetName).toBe('')
    expect(paste.invalid).toBe(true)
    paste.dialog.datasetName = 'Campaign'
    await paste.execute()
    await arrived()

    expect(app.data.batch.create).not.toHaveBeenCalled()
    expect(app.data.batch.copy).toHaveBeenCalledWith({
      sample_batch_id: 'b-src',
      dataset_id: 'ds-new',
      sample_batch_name: 'Morning QC',
      sample_batch_description: 'QC runs'
    })
    expect(app.data.dataset.focus).toHaveBeenCalledWith({ dataset_id: 'ds-new' })
  })

  it('is not offered in the system workspace', async () => {
    await clipboard.copy(SAMPLES)
    app.data.workspace.focused.is_system = true
    expect(paste.datasetValid).toBe(false)
  })

  it('does not create the dataset again when a retry follows a failed batch', async () => {
    await clipboard.copy(SAMPLES)
    app.data.batch.create.mockRejectedValueOnce(new Error('409'))

    paste.open({ dataset: true })
    paste.dialog.datasetName = 'Campaign'
    paste.dialog.batchName = 'Run 0'
    await paste.execute()

    expect(paste.dialog.visible).toBe(true)
    expect(app.data.sample.copy).not.toHaveBeenCalled()

    paste.dialog.batchName = 'Run 1'
    await paste.execute()

    expect(app.data.dataset.create).toHaveBeenCalledTimes(1)
    expect(app.data.batch.create).toHaveBeenLastCalledWith(
      expect.objectContaining({ dataset_id: 'ds-new', sample_batch_name: 'Run 1' })
    )
    expect(app.data.sample.copy).toHaveBeenCalledTimes(1)
    expect(paste.dialog.visible).toBe(false)
    await arrived()
    expect(app.data.dataset.focus).toHaveBeenCalledTimes(1)
  })

  it('creates nothing when the clipboard was emptied while the dialog was open', async () => {
    await clipboard.cut(SAMPLES)
    paste.open({ dataset: true })
    paste.dialog.datasetName = 'Campaign'
    paste.dialog.batchName = 'Run 1'

    await clipboard.clear()
    await paste.execute()

    expect(app.data.dataset.create).not.toHaveBeenCalled()
    expect(app.data.batch.create).not.toHaveBeenCalled()
    expect(paste.dialog.visible).toBe(false)
  })
})

describe('paste into a new batch', () => {
  beforeEach(() => {
    app.data.dataset.focusedId = 'ds-focused'
  })

  it('moves cut samples into a new batch of the focused dataset', async () => {
    await clipboard.cut(SAMPLES)
    expect(paste.batchValid).toBe(true)

    paste.open({ dataset: false })
    expect(paste.invalid).toBe(true)
    paste.dialog.batchName = 'Afternoon'
    await paste.execute()
    await arrived()

    expect(app.data.dataset.create).not.toHaveBeenCalled()
    expect(app.data.batch.create).toHaveBeenCalledWith(
      expect.objectContaining({ dataset_id: 'ds-focused', sample_batch_name: 'Afternoon' })
    )
    expect(app.data.sample.move).toHaveBeenCalledWith({
      sample_item_ids: ['s1', 's2'],
      sample_batch_id: 'b-new'
    })
    // a pasted cut is not offered again
    expect(clipboard.samples).toBeNull()
    expect(app.data.dataset.focus).not.toHaveBeenCalled()
    expect(app.data.batch.focus).toHaveBeenCalledWith({ sample_batch_id: 'b-new' })
  })

  it('focuses the new batch directly when its creation event arrived first', async () => {
    // The socket can add the record before the create request returns; a
    // lazy focus scheduled after that would wait for a list change that
    // never comes.
    app.data.batch.create.mockImplementation(async () => {
      app.data.batch.list = [{ sample_batch_id: 'b-new' }]
      return { data: { sample_batch_id: 'b-new' } }
    })
    await clipboard.copy(SAMPLES)
    paste.open({ dataset: false })
    paste.dialog.batchName = 'Afternoon'
    await paste.execute()

    expect(app.data.batch.focus).toHaveBeenCalledWith({ sample_batch_id: 'b-new' })
  })

  it('does not focus the new batch before it is in the list, nor after giving up', async () => {
    vi.useFakeTimers()
    try {
      app.data.batch.create.mockResolvedValue({ data: { sample_batch_id: 'b-new' } })
      await clipboard.copy(SAMPLES)
      paste.open({ dataset: false })
      paste.dialog.batchName = 'Afternoon'
      await paste.execute()
      expect(app.data.batch.focus).not.toHaveBeenCalled()

      vi.advanceTimersByTime(61_000)
      app.data.batch.list = [{ sample_batch_id: 'b-new' }]
      await nextTick()
      expect(app.data.batch.focus).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('is not offered for a copied batch', async () => {
    await clipboard.copy(BATCH)
    expect(paste.batchValid).toBe(false)
  })
})
