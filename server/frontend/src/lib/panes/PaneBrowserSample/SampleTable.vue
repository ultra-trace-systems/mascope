<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useWindowSize } from '@vueuse/core'

import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'

import { FilterMatchMode } from '@primevue/core/api'

import { BaseTabbedPanel, BaseMatchTag, BaseCopyableField } from '@/lib/base'
import { DialogSampleOp, DialogCalibration } from '@/lib/dialogs'
import { calibrationStatus } from '@/lib/calibrationStatus'
import { clone } from '@/lib/utils'
import { num } from '@/lib/formatters'
import { useApp } from '@/stores'

import SampleTableCustomizer from './SampleTableCustomizer.vue'
import SampleContextMenu from './SampleContextMenu.vue'
import {
  useBatchStatus,
  useCustomizerPopover,
  useSampleContextMenu,
  useSampleScroller
} from './stores'

const app = useApp()
const sampleTable = ref(null)
const scroller = useSampleScroller()

const customizer = useCustomizerPopover()
const contextMenu = useSampleContextMenu()

const batch = computed(() => app.data.batch.focused)
const samples = computed(
  () =>
    app.data.sample.list?.filter((sample) => sample.sample_batch_id == app.data.batch.focusedId) ??
    []
)

const filters = ref({
  global: { value: null, matchMode: FilterMatchMode.CONTAINS }
})

// Track filtered count for display
const filteredCount = ref(0)

// Update filtered count when filter event fires
const onFilter = (event) => {
  filteredCount.value = event.filteredValue?.length ?? 0
}

// Initialize/reset filtered count when samples change
watch(
  samples,
  (newSamples) => {
    filteredCount.value = newSamples.length
  },
  { immediate: true }
)

const batchStatus = computed(() => {
  if (!batch.value) return null

  const batchStatusStore = useBatchStatus()
  return {
    status: batch.value.status,
    config: batchStatusStore.config,
    // A "recalibrate" batch needs its m/z calibration re-fit (e.g. after its
    // ionization mode's calibration collection changed), so open the calibration
    // dialog for it; otherwise refresh matches.
    onRematch: () => {
      if (batch.value.status === 'recalibrate') {
        contextMenu.row = batch.value
        contextMenu.dialog.calibration = true
      } else {
        app.data.batch.rematch({ sample_batch_id: batch.value.sample_batch_id })
      }
    }
  }
})

// Watch for table ref to become available and bind to scroller
watch(
  sampleTable,
  (newTableRef) => {
    if (newTableRef) {
      scroller.bind(
        newTableRef,
        () => samples.value,
        () => customizer.config
      )
    }
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  scroller.bind(
    null,
    () => [],
    () => ({ sortField: null, sortOrder: 1 })
  )
})

// Handle Ctrl+A to select all rows (virtual scroller only selects visible rows by default)
const onKeyDown = (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'a') {
    event.preventDefault()
    // Read processedData imperatively (not reactively) to get filtered rows
    const filteredRows = sampleTable.value?.processedData ?? samples.value
    app.data.sample.selected = [...filteredRows]
  }
}

const { height } = useWindowSize()
const padding = 100
const tableHeight = computed(() => ((height.value - padding) * app.ui.split.top) / 100 - 50)

// Open the calibration dialog for one sample (badge click), reusing the
// context-menu "Recalibrate" plumbing.
const openCalibration = (sample) => {
  contextMenu.row = sample
  contextMenu.dialog.calibration = true
}
</script>

<template>
  <BaseTabbedPanel
    :label="
      filters.global?.value
        ? `Samples (${filteredCount}/${samples.length})`
        : `Samples (${samples.length})`
    "
    icon="pi pi-tags"
    :error="app.data.sample.error"
    :onRetry="() => app.data.sample.load('retry')"
    :clear="app.data.batch.unfocus"
    :back-label="'Back to batches'"
    :contextMenu="contextMenu"
    :loading="app.data.sample.pending"
    :status="batchStatus"
    :pt="
      app.ui.help.right(
        `
        <h1>Sample Browser: Samples</h1>

        <p>Shows all samples in the currently opened batch (${app.data.batch.focused.sample_batch_name}).</p>

        <p>Click on a sample to select it. Hold Shift or Ctrl to select multiple samples.</p>

        <p>Right click on selected sample(s) to perform actions such as copy, cut and delete.</p>
      `,
        {
          doc: app.ui.help.docUrl(
            'guides/import-files/#build-your-batch-from-the-acquisition-samples'
          )
        }
      )
    "
  >
    <template #menu>
      <div class="row">
        <IconField>
          <InputIcon>
            <i class="pi pi-search" />
          </InputIcon>
          <InputText v-model="filters['global'].value" type="text" placeholder="Search samples" />
        </IconField>
        <SampleTableCustomizer />
      </div>
    </template>
    <DataTable
      ref="sampleTable"
      v-if="samples.length > 0 && batch"
      :value="samples"
      dataKey="sample_item_id"
      selectionMode="multiple"
      :metaKeySelection="true"
      v-model:selection="app.data.sample.selected"
      v-model:contextMenuSelection="contextMenu.selection"
      contextMenu
      @rowContextmenu="
        async (event) => {
          event.originalEvent.stopPropagation() // don't trigger batch context menu
          event.originalEvent.preventDefault() // don't open default context menu
          await contextMenu.onClick(event)
        }
      "
      @keydown="onKeyDown"
      reorderableColumns
      resizableColumns
      :sortField="customizer.config.sortField"
      :sortOrder="customizer.config.sortOrder"
      v-model:filters="filters"
      @filter="onFilter"
      @sort="
        ({ sortField, sortOrder }) => {
          customizer.config.sortField = sortField
          customizer.config.sortOrder = sortOrder
        }
      "
      @column-reorder="
        ({ dragIndex, dropIndex }) => {
          let columns = clone(customizer.config.columns)
          const column = columns.splice(dragIndex - 1, 1)[0]
          columns.splice(dropIndex - 1, 0, column)
          customizer.config.columns = columns
        }
      "
      size="small"
      scrollable
      :scrollHeight="`${tableHeight}px`"
      :virtualScrollerOptions="{ itemSize: 35.74 }"
      :pt="{ bodyRow: ({ context }) => ({ id: samples[context.index]?.sample_item_id }) }"
    >
      <Column sortable sortField="match.match_score" class="match-column">
        <template #header>
          <span class="pi ph ph-seal-percent" />
        </template>
        <template #body="{ data }">
          <BaseMatchTag
            :match-score="data.match?.match_score"
            :match-category="data.match?.match_category"
            :alarming="data.match?.alarming"
            :tooltip="
              data.match?.sample_peak_intensity_sum
                ? `Total peak intensity: ${num.peakIntensity.format(data.match.sample_peak_intensity_sum)} (cps)`
                : 'No peak intensity data'
            "
          />
        </template>
      </Column>

      <Column class="calibration-column">
        <template #header>
          <span class="pi ph ph-scales" v-tooltip.top="'m/z calibration status'" />
        </template>
        <template #body="{ data }">
          <span
            v-if="calibrationStatus(data.mz_calibration)"
            class="pi ph ph-scales"
            :class="[
              `calibration-${calibrationStatus(data.mz_calibration).state}`,
              { 'calibration-badge': calibrationStatus(data.mz_calibration).clickable }
            ]"
            v-tooltip="{
              value: calibrationStatus(data.mz_calibration).tooltip,
              showDelay: 500
            }"
            @click.stop="calibrationStatus(data.mz_calibration).clickable && openCalibration(data)"
          />
        </template>
      </Column>

      <template v-for="{ field, label, kind } in customizer.config.columns" :key="field">
        <Column v-if="kind == 'standard'" :field="field" :header="label" sortable>
          <template #body="{ data }">
            <span class="field">
              <BaseCopyableField :field="data[field]" />
            </span>
          </template>
        </Column>
        <Column v-if="kind == 'custom'" field="sample_item_attributes" :header="label" sortable>
          <template #body="{ data }">
            <BaseCopyableField :field="data.sample_item_attributes[field]" />
          </template>
        </Column>
      </template>
    </DataTable>
    <i v-else style="padding-left: 3em; margin-top: 1rem; line-height: 2rem">
      Empty - no sample items
    </i>
    <!-- modals etc. -->
    <DialogSampleOp v-model:action="contextMenu.dialog.op" :item="contextMenu.row" />
    <DialogCalibration
      v-model:visible="contextMenu.dialog.calibration"
      :context="contextMenu.row"
    />
    <SampleContextMenu />
  </BaseTabbedPanel>
</template>
