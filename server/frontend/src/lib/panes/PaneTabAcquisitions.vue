<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'

import Button from 'primevue/button'
import Select from 'primevue/select'
import DatePicker from 'primevue/datepicker'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'
import FloatLabel from 'primevue/floatlabel'
import ContextMenu from 'primevue/contextmenu'
import { useConfirm } from 'primevue/useconfirm'

import { UppyContextProvider } from '@uppy/vue'
import '@uppy/core/css/style.min.css'
import Dashboard from '@uppy/dashboard'
import '@uppy/dashboard/css/style.min.css'
import DropTarget from '@uppy/drop-target'
import '@uppy/drop-target/css/style.min.css'

import {
  DialogSampleOp,
  DialogBatchImport,
  DialogFileUpload,
  DialogIonizationOp
} from '@/lib/dialogs'
import { InstrumentSelector } from '@/lib/toolbars'

import { api } from '@/api'
import { useApp } from '@/stores'

const app = useApp()
const uppy = app.uppy.get()
const confirm = useConfirm()

onMounted(() => {
  uppy
    .use(DropTarget, { target: '#uppy-drop-target' })
    .on('file-added', () => {
      const dashboard = uppy.getPlugin('Dashboard')
      if (dashboard) {
        dashboard.openModal()
      }
    })
    .use(Dashboard, {
      trigger: '#uppy-upload-trigger',
      inline: false,
      height: 600,
      showProgressDetails: true,
      theme: app.ui.darkmode.active ? 'dark' : 'light',
      closeModalOnClickOutside: true,
      closeAfterFinish: true,
      proudlyDisplayPoweredByUppy: false,
      note: 'Supported file types: .h5 (TOF), .raw (Orbi)'
    })
})

onUnmounted(() => {
  uppy.removePlugin(uppy.getPlugin('Dashboard'))
  uppy.removePlugin(uppy.getPlugin('DropTarget'))
})

defineProps({
  active: {
    type: Boolean
  }
})

const dialog = reactive({
  sample: null,
  batchImport: false,
  mechanism: null
})

const contextMenuRef = ref(null)
const contextMenuItems = ref([
  {
    label: 'Download',
    icon: 'pi pi-download',
    command: () => {
      api.http.post(
        `/file/download`,
        {
          sample_file_ids: app.data.acquisition.selected.map(({ sample_file_id }) => sample_file_id)
        },
        {
          use: 'process',
          type: 'download_sample_files'
        }
      )
    }
  },
  {
    label: 'Delete',
    icon: 'pi pi-trash',
    command: () => {
      const sample_file_ids = app.data.acquisition.selected.map(
        ({ sample_file_id }) => sample_file_id
      )
      if (sample_file_ids.length > 0) {
        confirm.require({
          message: `Are you sure you want to delete the selected sample files?
            This action cannot be undone.`,
          header: 'Delete Sample Files',
          icon: 'pi pi-exclamation-triangle',
          rejectProps: {
            label: 'Cancel',
            severity: 'secondary',
            outlined: true
          },
          acceptProps: {
            label: 'Delete',
            severity: 'danger'
          },
          accept: () => {
            api.http.post(
              `/sample/files/delete`,
              { sample_file_ids },
              {
                use: 'delete',
                type: 'delete_sample_files'
              }
            )
          }
        })
      }
    }
  },
  {
    label: 'Re-process',
    icon: 'pi pi-refresh',
    command: () => {
      const sample_file_ids = app.data.acquisition.selected.map(
        ({ sample_file_id }) => sample_file_id
      )
      confirm.require({
        message: `Are you sure you want to re-process the selected files?
          This will delete existing acquisition data and recreate it. The action is
          only available for files not associated with user-created batches.

          Note, the files will be processed according to the currently defined ionization modes.
          `,
        header: 'Re-process Sample Files',
        icon: 'pi pi-question-circle',
        rejectProps: {
          label: 'Cancel',
          severity: 'secondary',
          outlined: true
        },
        acceptProps: {
          label: 'Re-process',
          severity: 'info'
        },
        accept: () => {
          api.http.post(
            `/sample/files/reprocess`,
            { sample_file_ids },
            {
              use: 'process',
              type: 'reprocess_sample_files'
            }
          )
        }
      })
    }
  }
])
const contextMenuRow = ref(null)

const search = ref('')
const polarityDropdown = ref('')

// Client-side filters narrow the current page. Cross-page search/polarity
// requires server-side filter push-down - tracked as a follow-up to #1354.
const acquisitions = computed(
  () =>
    app.data.acquisition.list?.filter(
      ({ filename, polarity }) =>
        filename.toLowerCase().includes(search.value.toLowerCase()) &&
        (polarityDropdown.value === '' ||
          polarityDropdown.value === '+-' ||
          polarity === polarityDropdown.value ||
          polarity === '+-')
    ) ?? []
)

// Client-side filter narrows only the visible page - when filtering actually trims
// something display a hint to user so  that an empty search result
// is not mistaken for "no matches anywhere".
const filterActive = computed(
  () => search.value !== '' || !['', '+-'].includes(polarityDropdown.value)
)
const filteredCount = computed(() => acquisitions.value.length)
const pageCount = computed(() => app.data.acquisition.list?.length ?? 0)

// Force DataTable remount on page/page-size change so PrimeVue's internal
// selection anchor resets. Without this the anchor from a previous page
// can carry over and produce range-select artifacts.
const tableKey = computed(() => `${app.data.acquisition.first}-${app.data.acquisition.rows}`)

const clearFilters = () => {
  app.data.acquisition.resetFilters()
  search.value = ''
  polarityDropdown.value = ''
}

// Check if files with both "+" and "-" polarities are selected
const hasBothPolarities = computed(() => {
  const polarities = app.data.acquisition.selected.filter(Boolean).map((s) => s.polarity)
  return polarities.includes('+') && polarities.includes('-')
})

// We consider "+-" as a mixed polarity type
const onlyMixedPolaritySelected = computed(() => {
  const selected = app.data.acquisition.selected.filter(Boolean)
  const allMixed = selected.every(({ polarity }) => polarity === '+-')
  const moreThanOneSelected = selected.length > 1
  const polarityNotSpecified = ['', '+-'].includes(polarityDropdown.value)
  return allMixed && moreThanOneSelected && polarityNotSpecified
})

// Watch for polarity changes and clear selected acquisitions
watch(polarityDropdown, () => {
  app.data.acquisition.selected = [] // Clear selected acquisitions
})

const derivedPolarity = computed(() => {
  if (['+', '-'].includes(polarityDropdown.value)) {
    return polarityDropdown.value
  }
  const selected = app.data.acquisition.selected.filter(Boolean)
  const positive = selected.every(({ polarity }) => ['+', '+-'].includes(polarity))
  const negative = selected.every(({ polarity }) => ['-', '+-'].includes(polarity))
  if (positive) return '+'
  if (negative) return '-'
  return null
})

// --- "Process selected" button tooltip and disabled state.
const processBlockedReason = computed(() => {
  if (!app.data.batch.focused) return 'Select a batch to process the files.'
  if (app.data.acquisition.selected?.length === 0) {
    return 'Select acquisitions to process.'
  }
  if (hasBothPolarities.value) {
    return 'Cannot process files with both "+" and "-" polarities selected.'
  }
  if (onlyMixedPolaritySelected.value) {
    return 'Only mixed polarity files selected. Choose a polarity from the dropdown.'
  }
  return null
})
const processDisabled = computed(() => processBlockedReason.value !== null)

// --- paginator configuration
// Plain string template - the object-based responsive form has long-standing
// PrimeVue bugs that crash the Paginator on update (issues #5604, #6595).
const rowsPerPageOptions = [10, 50, 100, 200, 500]
const paginatorTemplate =
  'FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink RowsPerPageDropdown CurrentPageReport'
const currentPageReportTemplate =
  '{first}-{last} of {totalRecords} · page {currentPage}/{totalPages}'
</script>

<template>
  <div class="pane-wrapper">
    <menu class="acquisition-menu">
      <div class="row">
        <Button
          v-tooltip.top="'Edit ionizations'"
          label="Edit ionizations"
          class="hiddenlabel"
          icon="pi pi-sliders-h"
          text
          size="small"
          @click="
            () => {
              dialog.mechanism = true
            }
          "
        />
        <InstrumentSelector />
      </div>
      <Select
        inputId="time"
        v-model="app.data.acquisition.time.mode"
        :options="['Last 24 hours', 'Last 7 days', 'Last 30 days', 'Last 90 days']"
        style="flex-direction: row-reverse"
        placeholder="Recent"
      />
      <FloatLabel class="datepicker-label">
        <label>Min. Datetime</label>
        <DatePicker
          v-model="app.data.acquisition.time.range.min"
          inputId="min-datetime"
          dateFormat="yy/mm/dd"
          showTime
          showIcon
          :class="'full ' + (app.data.acquisition.time.mode == 'range' ? '' : 'inactive')"
        />
      </FloatLabel>
      <FloatLabel class="datepicker-label">
        <label>Max. Datetime</label>
        <DatePicker
          v-model="app.data.acquisition.time.range.max"
          inputId="max-datetime"
          dateFormat="yy/mm/dd"
          showTime
          showIcon
          :class="'full ' + (app.data.acquisition.time.mode == 'range' ? '' : 'inactive')"
        />
      </FloatLabel>
      <Select
        inputId="polarity"
        v-model="polarityDropdown"
        :options="['+-', '+', '-']"
        style="max-width: 100px"
        placeholder="Polarity"
      />
      <div class="search-cell">
        <FloatLabel style="width: 100%">
          <IconField class="full">
            <InputIcon>
              <i class="pi pi-search" />
            </InputIcon>
            <InputText v-model="search" placeholder="Search filenames" style="width: 100%" />
          </IconField>
        </FloatLabel>
        <small :class="['filter-hint', { hidden: !filterActive }]">
          {{ filteredCount }} of {{ pageCount }} shown (filtered)
        </small>
      </div>
      <Button
        icon="pi pi-filter-slash"
        @click="clearFilters"
        text
        severity="secondary"
        v-tooltip.left="'Clear filters and selection'"
      />
    </menu>
    <div class="pane-content">
      <UppyContextProvider :uppy="uppy">
        <div id="uppy-drop-target" class="uppy-container">
          <div class="table-container">
            <DataTable
              :key="tableKey"
              v-show="acquisitions?.length"
              :selection="app.data.acquisition.selected"
              @update:selection="
                (sel) => (app.data.acquisition.selected = (sel ?? []).filter(Boolean))
              "
              v-model:contextMenuSelection="contextMenuRow"
              :value="acquisitions"
              scrollable
              scrollHeight="flex"
              :sortField="app.data.acquisition.sortField"
              :sortOrder="app.data.acquisition.sortOrder"
              @sort="app.data.acquisition.setSort"
              size="small"
              selectionMode="multiple"
              dataKey="filename"
              :metaKeySelection="true"
              :virtualScrollerOptions="{ itemSize: 28 }"
              tableStyle="table-layout: fixed; width: 100%"
              lazy
              paginator
              :first="app.data.acquisition.first"
              :rows="app.data.acquisition.rows"
              :totalRecords="app.data.acquisition.total"
              :rowsPerPageOptions="rowsPerPageOptions"
              :paginatorTemplate="paginatorTemplate"
              :currentPageReportTemplate="currentPageReportTemplate"
              @page="app.data.acquisition.setPage"
              @rowContextmenu="
                (event) => {
                  contextMenuRow = event.data
                  event.originalEvent.preventDefault()
                  if (
                    !app.data.acquisition.selected.some(
                      ({ sample_file_id }) => sample_file_id === contextMenuRow.sample_file_id
                    )
                  ) {
                    app.data.acquisition.selected = [contextMenuRow]
                  }
                  contextMenuRef?.show(event.originalEvent)
                }
              "
            >
              <Column
                header="Filename"
                field="filename"
                sortable
                style="width: 60%"
                bodyClass="ellipsis-cell"
              >
                <template #body="{ data }">
                  <span :title="data.filename">{{ data.filename }}</span>
                </template>
              </Column>
              <Column header="Polarity" field="polarity" sortable style="width: 90px" />
              <Column header="Datetime" field="datetime" sortable style="width: 180px" />
              <template #paginatorstart>
                <strong v-if="app.data.acquisition.multiselected" style="font-style: italic">
                  {{ app.data.acquisition.selected.length }} files selected
                </strong>
              </template>
            </DataTable>
            <div v-if="!acquisitions?.length" class="center" style="min-height: 150px">
              <i class="info-line">
                <span class="pi pi-inbox" /><span>No raw files found with current filters</span>
              </i>
            </div>
          </div>
          <div class="bottom-section">
            <i class="info-line">
              <span class="pi pi-file-arrow-up" /><span>Drag sample files here to upload them</span>
            </i>
            <menu class="bottom-menu">
              <Button
                id="uppy-upload-trigger"
                label="Upload"
                icon="pi pi-file-arrow-up"
              />
              <Button
                v-tooltip.top="processBlockedReason"
                label="Process selected"
                icon="pi pi-file-plus"
                :disabled="processDisabled"
                @click="
                  () => {
                    if (app.data.acquisition.focused) {
                      dialog.sample = 'create'
                    } else if (app.data.acquisition.multiselected) {
                      dialog.batchImport = true
                    }
                  }
                "
              />
            </menu>
          </div>
        </div>
      </UppyContextProvider>

      <DialogSampleOp
        v-model:action="dialog.sample"
        :item="app.data.acquisition.focused"
        @submit="app.data.acquisition.unfocus()"
      />
      <DialogBatchImport
        v-model:visible="dialog.batchImport"
        :files="app.data.acquisition.selected"
        :polarity="derivedPolarity"
        @submit="app.data.acquisition.unfocus()"
      />
      <DialogFileUpload
        :files="app.uppy.invalidFiles"
        @upload="
          $event.map((file) => {
            try {
              uppy.addFile(file)
            } catch (error) {
              uppy.info(error, 'error')
            }
          })
        "
      />
      <DialogIonizationOp v-model:visible="dialog.mechanism" />
      <ContextMenu :model="contextMenuItems" ref="contextMenuRef" />
    </div>
  </div>
</template>

<style scoped>
.pane-content {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.search-cell {
  position: relative;
  flex-grow: 1;
  max-width: 250px;
  margin-bottom: 1.2em;
}

.filter-hint {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 0.15rem;
  font-size: 11px;
  font-style: italic;
  opacity: 0.6;
  line-height: 1.2;
  white-space: nowrap;
}
.filter-hint.hidden {
  visibility: hidden;
}

.uppy-container {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.table-container {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.table-container :deep(.p-datatable) {
  height: 100%;
  display: flex;
  flex-direction: column;
  user-select: none;
}

.table-container :deep(.p-datatable-table-container) {
  flex: 1;
  min-height: 0;
}

.table-container :deep(.ellipsis-cell) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 0;
}

/* --- Paginator: drop border + background, pin controls to true center
   regardless of paginatorstart/paginatorend slot widths. */
.table-container :deep(.p-paginator) {
  position: relative;
  border: none;
  background: transparent;
  padding: 0.5rem 0;
  justify-content: center;
}

/* The paginator-start / -end wrappers sit absolute on the sides so they
   don't shift the centered controls when their content width changes. */
.table-container :deep(.p-paginator-content-start) {
  position: absolute;
  left: 0.5rem;
}
.table-container :deep(.p-paginator-content-end) {
  position: absolute;
  right: 0.5rem;
}

.bottom-section {
  flex-shrink: 0;
  padding-top: 0.5rem;
}

.acquisition-menu {
  gap: 0.5rem;
  align-items: baseline;
  height: fit-content;
  width: 100%;
  max-width: 100%;
  margin-bottom: 1rem;
  flex-wrap: wrap;
}

.datepicker-label {
  flex: 0 0 auto;
  width: 160px;
}

.datepicker-label :deep(.p-datepicker-input) {
  width: 100%;
}

.bottom-menu {
  gap: 1rem;
  align-items: baseline;
  height: fit-content;
  width: 100%;
  margin-bottom: 1rem;
  justify-content: flex-end;
}

menu {
  display: flex;
  flex-flow: row wrap;
  gap: 0.5rem;
  justify-content: left;
  align-items: center;
  padding: 0;
  margin: 0;
}

menu > :deep(*) {
  margin-bottom: 0;
}

menu :deep(*) {
  font-size: 12px;
}

menu :deep(.full) {
  width: 100%;
}

.inactive {
  opacity: 0.5;
}

.info-line {
  width: 100%;
  display: flex;
  flex-flow: row;
  justify-content: center;
  align-items: center;
  opacity: 0.5;
  gap: 0.5rem;
  margin: 0.5rem;
}
</style>
