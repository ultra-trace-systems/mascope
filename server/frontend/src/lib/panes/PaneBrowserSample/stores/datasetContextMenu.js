import { ref, reactive, computed } from 'vue'
import { defineStore } from 'pinia'

import { useConfirm } from 'primevue/useconfirm'

import { useApp } from '@/stores'

import { useClipboard } from './clipboard.js'

export const useDatasetContextMenu = defineStore('browser.sample.datasetCtxMenu', () => {
  const app = useApp()
  const confirm = useConfirm()

  // local deps
  const clipboard = useClipboard()

  // state
  const menu = ref()
  const row = ref(null)
  const selection = ref(null)
  const dialog = reactive({
    op: null
  })

  // paste is valid for a cut dataset originating from a different workspace
  const pasteValid = computed(
    () =>
      clipboard.op === 'cut' &&
      clipboard.dataset !== null &&
      clipboard.dataset.workspace_id !== app.data.workspace.focusedId &&
      !app.data.workspace.focused?.is_system
  )

  // actions
  async function onClick(event) {
    await clipboard.read()
    row.value = event?.data ?? null
    // show on a row (cut/edit/delete) or when a paste is available (empty space)
    if (row.value || pasteValid.value) {
      show(event)
    } else {
      hide()
    }
  }
  function show(event) {
    menu.value?.show(event?.originalEvent ?? event)
  }
  function hide() {
    menu.value?.hide()
    row.value = null
  }
  function clear() {
    selection.value = null
  }

  // context menu entries
  const entries = computed(() => [
    {
      label: 'Paste dataset',
      icon: 'pi pi-clipboard',
      visible: pasteValid.value,
      command: async () => {
        await app.data.dataset.move({
          dataset_id: clipboard.dataset.dataset_id,
          source_workspace_id: clipboard.dataset.workspace_id,
          target_workspace_id: app.data.workspace.focusedId
        })
        clipboard.clear()
      }
    },
    {
      separator: true,
      visible: pasteValid.value && row.value !== null
    },
    {
      label: 'Cut dataset',
      icon: 'pi ph ph-scissors',
      // ACQUISITION datasets are auto-managed - never movable
      visible: row.value !== null && row.value?.dataset_type !== 'ACQUISITION',
      command: () => {
        clipboard.cut({
          dataset_id: row.value.dataset_id,
          workspace_id: row.value.workspace_id,
          dataset_name: row.value.dataset_name
        })
      }
    },
    {
      label: 'Edit dataset',
      icon: 'pi pi-pen-to-square',
      visible: row.value !== null,
      command: () => {
        dialog.op = 'edit'
      }
    },
    {
      label: 'Delete dataset',
      icon: 'pi pi-trash',
      visible: row.value !== null,
      command: () => {
        dialog.op = 'delete'
      }
    },
    { separator: true, visible: row.value !== null },
    {
      label: 'Process',
      icon: 'pi ph ph-hourglass-medium',
      visible: row.value !== null,
      items: [
        {
          label: 'Refresh matches',
          icon: 'pi ph ph-arrow-counter-clockwise',
          visible: row.value !== null,
          // The batch-level refresh for every batch in the dataset at once,
          // which is what saves the clicking this exists for. It runs one
          // batch at a time and cannot be stopped once started, so - unlike
          // the single-batch entry - it asks first.
          command: () => {
            const dataset = row.value
            confirm.require({
              icon: 'pi pi-info-circle',
              header: 'Refresh dataset matches',
              message:
                `Refresh matches for every batch in dataset ` +
                `"${dataset.dataset_name}"? Batches are processed one at a ` +
                `time and batches that are already up to date are skipped, ` +
                `so this can take a while on a large dataset.`,
              accept: () => {
                app.data.dataset.rematch({ dataset_id: dataset.dataset_id })
              },
              acceptProps: {
                icon: 'pi ph ph-arrow-counter-clockwise',
                label: 'Refresh'
              },
              rejectProps: {
                icon: 'pi pi-times',
                label: 'Cancel',
                severity: 'secondary'
              }
            })
          }
        }
      ]
    }
  ])

  return {
    ref: menu,
    onClick,
    row,
    show,
    hide,
    selection,
    clear,
    entries,
    dialog
  }
})
