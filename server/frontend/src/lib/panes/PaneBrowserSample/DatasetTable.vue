<script setup>
import { ref, computed, watch } from 'vue'

import { useWindowSize } from '@vueuse/core'

import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'

import { BaseTabbedPanel, BaseCopyableField } from '@/lib/base'
import { useApp } from '@/stores'

import DatasetContextMenu from './DatasetContextMenu.vue'
import { useDatasetContextMenu, useDatasetTableConfig } from './stores'

const app = useApp()
const contextMenu = useDatasetContextMenu()
const datasetTable = useDatasetTableConfig()

// Track filtered count for display
const filteredCount = ref(0)

// Handle filter event
const onFilter = (event) => {
  filteredCount.value = (event.filteredValue ?? []).length
}

// Initialize when dataset list changes
watch(
  () => app.data.dataset.list,
  (newList) => {
    filteredCount.value = (newList ?? []).length
  },
  { immediate: true }
)

const { height } = useWindowSize()
const padding = 100
const tableHeight = computed(() => ((height.value - padding) * app.ui.split.top) / 100 - 50)
</script>

<template>
  <BaseTabbedPanel
    v-if="app.data.dataset.list"
    :label="
      datasetTable.config.filters?.global?.value
        ? `Datasets (${filteredCount}/${app.data.dataset.list?.length})`
        : `Datasets (${app.data.dataset.list?.length})`
    "
    icon="pi ph ph-folders"
    :contextMenu="contextMenu"
    :pt="
      app.ui.help.right(`
        <h1>Sample Browser: Datasets</h1>

        <p>Shows all datasets in the currently selected workspace
        (${app.data.workspace.focused?.workspace_name}).
        </p>

        <p>Click on a dataset to open it and see the batches within.</p>

        <p>Right click on a dataset to cut, edit or delete it. Right click
        on empty space to paste a cut dataset into this workspace.</p>
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
            v-model="datasetTable.config.filters['global'].value"
            type="text"
            placeholder="Search datasets"
          />
        </IconField>
        <Button
          v-tooltip.top="'Create dataset'"
          label="Create dataset"
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
      :value="app.data.dataset.list"
      dataKey="dataset_id"
      selectionMode="single"
      :metaKeySelection="false"
      v-model:selection="app.data.dataset.focused"
      v-model:contextMenuSelection="contextMenu.selection"
      contextMenu
      @rowContextmenu="
        async (event) => {
          event.originalEvent.stopPropagation()
          event.originalEvent.preventDefault()
          await contextMenu.onClick(event)
        }
      "
      resizableColumns
      :sortField="datasetTable.config.sortField"
      :sortOrder="datasetTable.config.sortOrder"
      v-model:filters="datasetTable.config.filters"
      @filter="onFilter"
      @sort="
        ({ sortField, sortOrder }) => {
          datasetTable.config.sortField = sortField
          datasetTable.config.sortOrder = sortOrder
        }
      "
      size="small"
      scrollable
      :scrollHeight="`${tableHeight}px`"
      :virtualScrollerOptions="{ itemSize: 35.74 }"
    >
      <Column header="Dataset" field="dataset_name" sortable>
        <template #body="{ data }">
          <div class="row" style="justify-content: flex-start">
            <span :class="'pi ph ph-folder'" style="opacity: 0.4" />
            <BaseCopyableField
              :field="data.dataset_name"
              v-tooltip="{ value: data.dataset_description, showDelay: 1000 }"
            />
          </div>
        </template>
      </Column>
    </DataTable>
  </BaseTabbedPanel>
  <DatasetContextMenu />
</template>
