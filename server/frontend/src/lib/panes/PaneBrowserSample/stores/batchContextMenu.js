import { ref, reactive, computed } from 'vue'
import { defineStore } from 'pinia'

import { useConfirm } from 'primevue/useconfirm'

import { useApp } from '@/stores'
import { useBatchDeleteDialog } from '@/lib/dialogs'
import { generateCopyName } from '@/api/utils'
import { batchInstruments, canCalibrateInstruments } from '@/lib/permissions'

import { useSampleContextMenu } from './sampleContextMenu.js'
import { useCustomizerPopover } from './customizerPopover.js'
import { useClipboard } from './clipboard.js'

export const useBatchContextMenu = defineStore('browser.sample.batchCtxMenu', () => {
  const app = useApp()
  const confirm = useConfirm()

  // local deps
  const sampleContextMenu = useSampleContextMenu()
  const customizerPopover = useCustomizerPopover()
  const clipboard = useClipboard()

  // state
  const menu = ref()
  const row = ref()
  const selection = ref()
  const dialog = reactive({
    op: null,
    delete: useBatchDeleteDialog(),
    calibration: false
  })

  // actions
  async function onClick(event) {
    const targetBatch = event?.data ?? app.data.batch.focused ?? null

    // Don't open context menu if target batch is processing
    if (targetBatch?.status === 'processing') {
      return
    }

    await clipboard.read()
    row.value = targetBatch
    if (row.value || clipboard.batch !== null) {
      show(event)
    } else {
      hide()
    }
  }
  function show(event) {
    sampleContextMenu.hide()
    customizerPopover.hide()
    menu.value?.show(event?.originalEvent ?? event)
  }
  function hide() {
    menu.value?.hide()
    row.value = null
  }
  function clear() {
    selection.value = null
  }

  const pasteSamplesValid = computed(
    () =>
      row.value !== null &&
      clipboard.samples !== null &&
      (clipboard.op === 'copy' ||
        (clipboard.op === 'cut' &&
          clipboard.samples.every(
            ({ sample_batch_id }) => sample_batch_id !== row.value?.sample_batch_id
          )))
  )

  // The backend checks every instrument the batch draws on, so this does too.
  // The instruments come from the samples currently loaded for the batch; when
  // none are, the list is empty and the helper leaves the entry enabled rather
  // than hiding a capability it cannot rule out.
  const canCalibrate = computed(() =>
    canCalibrateInstruments(
      app.data.workspace.list,
      app.auth.user,
      batchInstruments(app.data.sample.list, row.value?.sample_batch_id)
    )
  )

  // context menu entries
  const entries = computed(() => [
    {
      label: 'Paste batch',
      icon: 'pi pi-clipboard',
      command: async () => {
        if (clipboard.batch) {
          if (clipboard.op === 'copy') {
            await app.data.batch.copy({
              sample_batch_id: clipboard.batch.sample_batch_id,
              dataset_id: app.data.dataset.focusedId,
              sample_batch_name: generateCopyName(clipboard.batch.sample_batch_name),
              sample_batch_description: clipboard.batch.sample_batch_description
            })
          }
        }
      },
      visible: clipboard.batch !== null && row.value === null
    },
    {
      label: `Paste sample${clipboard.samples?.length > 1 ? 's' : ''}`,
      icon: 'pi pi-clipboard',
      visible: pasteSamplesValid.value,
      command: async () => {
        if (clipboard.samples.length > 0) {
          if (clipboard.op === 'copy') {
            await app.data.sample.copy({
              sample_item_ids: clipboard.samples.map((s) => s.sample_item_id),
              sample_batch_id: row.value.sample_batch_id
            })
          } else if (clipboard.op === 'cut') {
            await app.data.sample.move({
              sample_item_ids: clipboard.samples.map((s) => s.sample_item_id),
              sample_batch_id: row.value.sample_batch_id
            })
            clipboard.clear()
          }
        }
      }
    },
    {
      separator: true,
      visible: pasteSamplesValid.value
    },
    {
      label: 'Edit batch',
      icon: 'pi pi-pen-to-square',
      command: () => {
        dialog.op = 'update'
      },
      visible: row.value !== null
    },
    {
      label: 'Edit batch targets',
      icon: 'pi pi-bullseye',
      command: () => {
        dialog.op = 'update_targets'
      },
      visible: row.value !== null
    },
    {
      label: 'Copy batch',
      icon: 'pi pi-copy',
      command: () => {
        clipboard.copy(row.value)
      },
      visible: row.value !== null
    },
    {
      label: 'Delete batch',
      icon: 'pi pi-trash',
      command: () => dialog.delete(row.value),

      visible: row.value !== null
    },
    { separator: true, visible: row.value !== null },
    {
      label: `Download`,
      icon: 'pi pi-file-export',
      visible: row.value !== null,
      items: [
        {
          label: 'Batch data',

          icon: 'pi ph ph-table',
          command: async () => {
            await app.data.batch.exportSpreadsheet(row.value)
          },
          visible: row.value !== null
        },
        {
          label: 'Peak data',
          icon: 'pi pi-wave-pulse',
          command: () => {
            confirm.require({
              icon: 'pi pi-info-circle',
              header: 'Export batch peak data',
              message: `Export peak data for batch "${row.value.sample_batch_name}"?`,
              accept: () => {
                app.data.batch.exportPeaks(row.value)
              },
              acceptProps: {
                icon: 'pi pi-file-export',
                label: 'Export'
              },
              rejectProps: {
                icon: 'pi pi-times',
                label: 'Cancel',
                severity: 'secondary'
              }
            })
          },
          visible: row.value !== null
        }
      ]
    },
    {
      label: 'Process',
      icon: 'pi ph ph-hourglass-medium',
      visible: row.value !== null,
      items: [
        {
          label: `Recalibrate`,
          icon: 'pi ph ph-scales',
          command: () => {
            dialog.calibration = true
          },
          disabled: !canCalibrate.value,
          visible: row.value !== null
        },
        {
          label: `Refresh matches`,
          icon: 'pi ph ph-arrow-counter-clockwise',
          command: () => app.data.batch.rematch(row.value),
          visible: row.value !== null
        },
        {
          label: `Rematch`,
          icon: 'pi ph ph-list-magnifying-glass',
          command: async () => {
            await app.data.batch.rematch({
              sample_batch_id: row.value.sample_batch_id,
              full_remove: true,
              force: true
            })
          },
          visible: row.value !== null
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
