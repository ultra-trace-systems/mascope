<script setup>
import { ref, computed, watch, onMounted } from 'vue'

import Button from 'primevue/button'
import Popover from 'primevue/popover'
import Listbox from 'primevue/listbox'
import SelectButton from 'primevue/selectbutton'

import { beautifySnakeCase } from '@/lib/utils'
import { useApp } from '@/stores'
import { runtime } from '@/lib/runtime'
import { peakAssignmentEnabled } from '@/lib/features'

import { useCustomizerPopover } from './stores'
import { STATUS_COLUMN, withStatusColumnIf } from './columnOrder.js'

const app = useApp()

const customizer = useCustomizerPopover()

const popoverRef = ref()

const tab = ref('All')

const availableColumns = computed(() => {
  const standard = [
    ...new Set(
      app.data.sample.list
        ?.map((item) => Object.keys(item ?? {}))
        .flat()
        .filter((field) => field !== 'sample_item_attributes')
    )
  ].map((field) => ({ field, kind: 'standard' }))
  const custom = [
    ...new Set(
      app.data.sample.list?.map((item) => Object.keys(item?.sample_item_attributes ?? {})).flat()
    )
  ].map((field) => ({ field, kind: 'custom' }))
  const describe = ({ field, kind }) => ({
    field,
    kind,
    label: createLabel(field),
    type: kind == 'custom' ? 'string' : inferType(field)
  })
  const listed = (entries) => entries.map(describe).filter(({ type }) => type !== 'object')
  // The status badge is a column of the table's own, not a field of the sample,
  // so it is listed rather than discovered - and only where peak assignment is
  // on, since there is no assignment status to show otherwise.
  return [
    ...listed(standard),
    ...(peakAssignmentEnabled ? [{ ...STATUS_COLUMN }] : []),
    ...listed([{ field: 'time', kind: 'custom' }, ...custom])
  ]
})

const runtimeConfig = runtime.config.sample_table_defaults

// The server's default columns, with the status badge in its default place
// unless the server names it somewhere itself, where peak assignment is on.
const defaultConfig = computed(() => ({
  columns: withStatusColumnIf(
    runtimeConfig.columns
      .map((col) => availableColumns.value.find(({ field }) => field === col))
      .filter((col) => !!col),
    peakAssignmentEnabled
  ),
  sortField: runtimeConfig.sort_field,
  sortOrder: runtimeConfig.sort_order
}))
const isDefault = computed(
  () => JSON.stringify(customizer.config) === JSON.stringify(defaultConfig.value)
)
const isInitialized = computed(() => JSON.stringify(customizer.config) !== '{}')

// local storage persistence

const STORAGE_KEY = 'mascope.browser.sample.tableConfig'

// Clean up legacy per-batch localStorage keys (runs once on component mount)
// TODO: Remove this function and associated code after a few releases
function cleanupLegacyKeys() {
  const keysToRemove = []
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)
    if (key?.startsWith('sample-browser-batch[')) {
      keysToRemove.push(key)
    }
  }
  keysToRemove.forEach((key) => localStorage.removeItem(key))
}

// write to local storage
function writeConfig() {
  if (isInitialized.value && !isDefault.value) {
    const newState = JSON.stringify(customizer.config)
    localStorage.setItem(STORAGE_KEY, newState)
  }
}
// read from local storage, falling back on default. A stored configuration
// from before the status badge was a column of its own has no entry for it and
// gets the badge in its default place rather than losing it; one stored while
// peak assignment was on loses the badge on a deployment that has it off.
function readConfig() {
  const storedState = localStorage.getItem(STORAGE_KEY)
  if (!storedState) {
    customizer.config = structuredClone(defaultConfig.value)
    return
  }
  const stored = JSON.parse(storedState)
  customizer.config = {
    ...stored,
    columns: withStatusColumnIf(stored.columns ?? [], peakAssignmentEnabled)
  }
}
// reset to default config and clear local storage
function resetConfig() {
  customizer.config = defaultConfig.value
  localStorage.removeItem(STORAGE_KEY)
}

// one-time legacy cleanup on mount
onMounted(() => {
  customizer.popover = popoverRef.value
  cleanupLegacyKeys()
})

// write to local storage when any options update
watch(() => customizer.config, writeConfig, { deep: true })

// read from local storage when a batch is loaded
watch(
  () => app.data.sample.list,
  (samples) => {
    // use sample count to figure out when loaded
    if (samples.length > 0) {
      readConfig()
    }
  }
)

// utils

function inferType(field) {
  const withField = app.data.sample.list.filter((item) => field in item)
  const types = [
    ...new Set(withField.map((item) => (item[field] ? typeof item[field] : 'null')))
  ].filter((type) => type !== 'null')
  return types.length == 1 ? types[0] : 'unknown'
}

function createLabel(field) {
  const custom = {
    index: '#',
    sample_item_name: 'Sample',
    filter_id: 'Filter'
  }
  if (field in custom) {
    return custom[field]
  } else {
    return beautifySnakeCase(field)
  }
}
</script>

<template v-if="app.data.batch.list">
  <Button
    v-tooltip.top="'Configure column visibility'"
    icon="pi pi-cog"
    severity="secondary"
    text
    size="small"
    @click="
      (event) => {
        event.stopPropagation()
        customizer.show(event)
      }
    "
  />
  <Popover ref="popoverRef" contentStyle="height: fit-content;">
    <div class="row" style="margin-bottom: 0.5rem">
      <SelectButton v-model="tab" :options="['All', 'Selected']" :allowEmpty="false" />
      <Button
        label="Reset"
        icon="pi pi-replay"
        severity="secondary"
        iconPos="right"
        text
        @click="resetConfig"
        v-tooltip.right="'Reset table configuration'"
      />
    </div>
    <Listbox
      v-model="customizer.config.columns"
      :options="
        availableColumns.filter(({ field }) =>
          tab === 'Selected'
            ? customizer.config.columns.map(({ field }) => field).includes(field)
            : true
        )
      "
      multiple
      optionLabel="label"
      filter
      dataKey="field"
      style="height: 230px"
    />
  </Popover>
</template>

<style scoped>
:deep(.p-listbox-list-container) {
  height: 180px;
}
</style>
