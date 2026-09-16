import { reactive, computed, watch } from 'vue'
import { defineStore } from 'pinia'

import { useApp } from '@/stores'
import { DEFAULT_SAMPLE_BATCH_TYPE, ANALYSIS_POLARITY } from '@/lib/constants'

import { useClipboard } from './clipboard.js'

// Pasting one level above where the clipboard contents belong: a batch into a
// workspace, or samples into a workspace or a dataset. The missing containers
// are created first - a dataset in the focused workspace, a batch in the new or
// the focused dataset - and the paste then goes into them as it would have had
// they existed already. The user lands in the new container.
export const usePasteIntoNew = defineStore('browser.sample.pasteIntoNew', () => {
  const app = useApp()
  const clipboard = useClipboard()

  const dialog = reactive({
    visible: false,
    // which containers the paste creates
    dataset: false,
    batch: false,
    datasetName: '',
    batchName: '',
    // containers this paste already created, kept for a retry after a later
    // step failed so the retry does not create them a second time
    createdDatasetId: null,
    createdBatchId: null,
    pending: false
  })

  // A copied batch, or copied or cut samples. A batch is only ever copied.
  const batchPaste = computed(() => clipboard.batch !== null && clipboard.op === 'copy')
  const samplesPaste = computed(
    () => clipboard.samples !== null && ['copy', 'cut'].includes(clipboard.op)
  )
  // a new dataset goes into the focused workspace, which the system one refuses
  const datasetValid = computed(
    () => (batchPaste.value || samplesPaste.value) && !app.data.workspace.focused?.is_system
  )
  const batchValid = computed(() => samplesPaste.value && app.data.dataset.focusedId != null)

  function open({ dataset }) {
    dialog.dataset = dataset
    // a pasted batch is its own batch; samples need one to go into
    dialog.batch = samplesPaste.value
    // a copied batch keeps its name, so it is the natural name for its dataset
    dialog.datasetName = batchPaste.value ? clipboard.batch.sample_batch_name : ''
    dialog.batchName = ''
    dialog.createdDatasetId = null
    dialog.createdBatchId = null
    dialog.visible = true
  }

  // The batch name may be left blank when the dataset is new too; one name
  // then serves both.
  const batchName = computed(() => dialog.batchName.trim() || dialog.datasetName.trim())
  const invalid = computed(
    () => (dialog.dataset && !dialog.datasetName.trim()) || (dialog.batch && !batchName.value)
  )

  // Open a container this paste created, once it is in its store's list. It
  // gets there either by a socket creation event, possibly before the request
  // that created it returns, or by the reload that follows focusing its parent.
  // A lazy focus is no use here: it resolves only on a reload, and a creation
  // event only appends the record. The wait is given up after a while so a
  // container that never shows up cannot pull the view away later.
  const LAND_TIMEOUT_MS = 60_000
  function land(store, key, id) {
    const present = () => store.list?.some((record) => record[key] === id)
    if (present()) {
      store.focus({ [key]: id })
      return
    }
    let timer = null
    const stop = watch(
      () => store.list,
      () => {
        if (!present()) return
        stop()
        clearTimeout(timer)
        store.focus({ [key]: id })
      }
    )
    timer = setTimeout(stop, LAND_TIMEOUT_MS)
  }

  async function execute() {
    if (invalid.value || dialog.pending) return
    // The clipboard can change while the dialog is open (another tab pasting
    // the cut drops it here); paste what is there when the paste is confirmed,
    // and create nothing if that is nothing.
    const batch = batchPaste.value ? clipboard.batch : null
    const samples = samplesPaste.value && !batch ? clipboard.samples : null
    const op = clipboard.op
    if (!batch && !samples) {
      dialog.visible = false
      return
    }
    dialog.pending = true
    try {
      let dataset_id = app.data.dataset.focusedId
      if (dialog.dataset) {
        if (!dialog.createdDatasetId) {
          const response = await app.data.dataset.create({
            dataset_name: dialog.datasetName.trim(),
            dataset_description: ''
          })
          dialog.createdDatasetId = response.data.dataset_id
          land(app.data.dataset, 'dataset_id', dialog.createdDatasetId)
        }
        dataset_id = dialog.createdDatasetId
      }

      if (batch) {
        await app.data.batch.copy({
          sample_batch_id: batch.sample_batch_id,
          dataset_id,
          sample_batch_name: batch.sample_batch_name,
          sample_batch_description: batch.sample_batch_description
        })
      } else if (samples) {
        if (!dialog.createdBatchId) {
          const response = await app.data.batch.create({
            dataset_id,
            sample_batch_name: batchName.value,
            sample_batch_description: '',
            sample_batch_type: DEFAULT_SAMPLE_BATCH_TYPE,
            polarity: ANALYSIS_POLARITY,
            target_collection_ids: []
          })
          dialog.createdBatchId = response.data.sample_batch_id
          land(app.data.batch, 'sample_batch_id', dialog.createdBatchId)
        }
        const body = {
          sample_item_ids: samples.map((s) => s.sample_item_id),
          sample_batch_id: dialog.createdBatchId
        }
        if (op === 'cut') {
          await app.data.sample.move(body)
          clipboard.clear()
        } else {
          await app.data.sample.copy(body)
        }
      }
    } catch {
      // The request layer has already shown what went wrong (a dataset name
      // the workspace already uses comes back as a 409). Keep the dialog open
      // so it can be corrected.
      return
    } finally {
      dialog.pending = false
    }
    dialog.visible = false
  }

  return {
    dialog,
    batchPaste,
    samplesPaste,
    datasetValid,
    batchValid,
    invalid,
    batchName,
    open,
    execute
  }
})
