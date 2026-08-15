<script setup>
import { ref, computed, watch } from 'vue'

import { useWindowSize } from '@vueuse/core'

import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'

import { BaseTabbedPanel, BaseCopyableField, BaseStatusIcon } from '@/lib/base'
import { useApp } from '@/stores'

import { useBatchContextMenu, useBatchTableConfig, useBatchStatus } from './stores'

const app = useApp()
const contextMenu = useBatchContextMenu()
const batchTable = useBatchTableConfig()
const batchStatusStore = useBatchStatus()

// Track filtered count for display
const filteredCount = ref(0)

// Handle filter event - update local count and store's filtered list
const onFilter = (event) => {
  const filteredList = event.filteredValue ?? []
  filteredCount.value = filteredList.length
  batchTable.filteredBatchList = filteredList
}

// Initialize when batch list changes (for when no filter is active)
watch(
  () => app.data.batch.list,
  (newList) => {
    const list = newList ?? []
    filteredCount.value = list.length
    batchTable.filteredBatchList = list
  },
  { immediate: true }
)

const { height } = useWindowSize()
const padding = 100
const tableHeight = computed(() => ((height.value - padding) * app.ui.split.top) / 100 - 50)

// Status-icon click handler. A "recalibrate" batch needs its m/z calibration
// re-fit (e.g. after its ionization mode's calibration collection changed), so
// open the calibration dialog for it - reusing the same flow as the context
// menu's "Recalibrate" action. Any other actionable status (rematch) refreshes
// matches.
const onStatusAction = (batch) => {
  if (batch.status === 'recalibrate') {
    contextMenu.row = batch
    contextMenu.dialog.calibration = true
  } else {
    app.data.batch.rematch({ sample_batch_id: batch.sample_batch_id })
  }
}
</script>

<template v-if="app.data.batch.list">
  <BaseTabbedPanel
    :label="
      batchTable.config.filters?.global?.value
        ? `Batches (${filteredCount}/${app.data.batch.list?.length})`
        : `Batches (${app.data.batch.list?.length})`
    "
    icon="pi pi-folder-open"
    :contextMenu="contextMenu"
    :pt="
      app.ui.help.right(`
        <h1>Sample Browser: Batches</h1>

        <p>Shows all batches in the currently selected dataset 
        (${app.data.dataset.focused.dataset_name}).
        </p>

        <p>Click on a batch to open it and see the samples within.</p>

        <p>Right click on a batch to perform actions.</p>
      `,
        { doc: app.ui.help.docUrl('concepts/#the-data-hierarchy') }
      )
    "
  >
    <template #menu>
      <div class="row">
        <IconField>
          <InputIcon>
            <i class="pi pi-search" />
          </InputIcon>
          <InputText
            v-model="batchTable.config.filters['global'].value"
            type="text"
            placeholder="Search batches"
          />
        </IconField>
        <Button
          v-tooltip.top="'Create batch'"
          label="Create batch"
          class="hiddenlabel"
          icon="pi pi-plus"
          text
          size="small"
          @click="
            () => {
              contextMenu.dialog.op = 'create'
            }
          "
        />
      </div>
    </template>
    <DataTable
      ref="batchDataTable"
      :value="app.data.batch.list"
      dataKey="sample_batch_id"
      selectionMode="single"
      :metaKeySelection="false"
      v-model:selection="app.data.batch.focused"
      v-model:contextMenuSelection="contextMenu.selection"
      contextMenu
      @rowContextmenu="
        async (event) => {
          event.originalEvent.stopPropagation() // don't trigger handler in <Panel> (see above)
          event.originalEvent.preventDefault() // don't open default context menu
          await contextMenu.onClick(event)
        }
      "
      resizableColumns
      :sortField="batchTable.config.sortField"
      :sortOrder="batchTable.config.sortOrder"
      v-model:filters="batchTable.config.filters"
      @filter="onFilter"
      @sort="
        ({ sortField, sortOrder }) => {
          batchTable.config.sortField = sortField
          batchTable.config.sortOrder = sortOrder
        }
      "
      size="small"
      scrollable
      :scrollHeight="`${tableHeight}px`"
      :virtualScrollerOptions="{ itemSize: 35.74 }"
    >
      <!-- batch columns -->
      <Column header="Batch" field="sample_batch_name" sortable>
        <template #body="{ data }">
          <div class="row" style="justify-content: flex-start">
            <!-- Batch status icons -->
            <div style="width: 20px; display: flex; justify-content: center; align-items: center">
              <BaseStatusIcon
                :status="data.status"
                :config="batchStatusStore.config"
                :onAction="() => onStatusAction(data)"
              />
            </div>
            <BaseCopyableField
              :field="data.sample_batch_name"
              v-tooltip="{ value: `${data.sample_batch_description}`, showDelay: 1000 }"
            />
          </div>
        </template>
      </Column>
    </DataTable>
  </BaseTabbedPanel>
</template>
