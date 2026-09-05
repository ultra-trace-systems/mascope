<script setup>
import ScrollPanel from 'primevue/scrollpanel'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Panel from 'primevue/panel'
import SelectButton from 'primevue/selectbutton'
import FloatLabel from 'primevue/floatlabel'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'

import { ref, computed, watch } from 'vue'

import { useApp } from '@/stores'
import { collectionTypes, getAllowedCollectionTypes } from '@/lib/constants'
import { beautifyConstant, instrumentType } from '@/lib/utils'

const app = useApp()

const selected = defineModel('selected')

const props = defineProps({
  mode: {
    default: 'targets',
    type: String
  },
  batch: {
    type: Object
  }
})

const search = ref()

const allowedTypes = computed(() => {
  if (props.mode === 'calibrants') {
    return ['CALIBRANTS'] // Only CALIBRANTS collections for calibrants mode
  }

  if (!props.batch?.type) {
    return collectionTypes // Show all types if batch type not set
  }

  // For targets mode, use batch type constraints
  let allowed = getAllowedCollectionTypes(props.batch.type)

  // Special case: TOF instruments can use CALIBRANTS for ACQUISITION batches.
  // The instrument list carries the class the reader recorded for each
  // instrument's files; the name rule is the fallback for a name it lacks.
  if (props.batch.type === 'ACQUISITION') {
    const currentInstrument = app.data.dataset.focused?.instrument
    const listed = app.data.instrument.list?.find((i) => i.instrument === currentInstrument)
    if ((listed?.type ?? instrumentType(currentInstrument)) === 'tof') {
      allowed = [...new Set([...allowed, 'CALIBRANTS'])]
    }
  }

  return allowed
})

const categoryOptions = computed(() => [
  ...collectionTypes.map((type) => ({
    label: beautifyConstant(type),
    value: beautifyConstant(type),
    disabled: !allowedTypes.value.includes(type)
  })),
  {
    label: 'All',
    value: 'All',
    disabled: allowedTypes.value.length <= 1
  }
])

const category = ref()

// Initialize category based on mode and available options
watch(
  allowedTypes,
  () => {
    const modeOption = beautifyConstant(props.mode.toUpperCase())
    category.value = allowedTypes.value.includes(props.mode.toUpperCase())
      ? modeOption
      : allowedTypes.value.length > 1
        ? 'All'
        : beautifyConstant(allowedTypes.value[0])
  },
  { immediate: true }
)
const targetCollections = computed(() =>
  app.data.target.collection.list
    .filter((coll) => {
      // Filter by allowed types for this batch
      if (!allowedTypes.value.includes(coll.target_collection_type)) return false

      // Filter by selected category
      if (
        category.value !== 'All' &&
        beautifyConstant(coll.target_collection_type) !== category.value
      ) {
        return false
      }

      // Filter by search
      const query = search.value?.toLowerCase() ?? ''
      return (
        coll.target_collection_name.toLowerCase().includes(query) ||
        coll.target_collection_description?.toLowerCase().includes(query)
      )
    })
    .map((coll) => ({
      ...coll,
      _outOfScope: coll.workspace_id !== null && coll.workspace_id !== app.data.workspace.focusedId
    }))
    .sort((a, b) => (a._outOfScope === b._outOfScope ? 0 : a._outOfScope ? 1 : -1))
)
</script>

<template>
  <Panel>
    <div class="row">
      <SelectButton
        v-model="category"
        :options="categoryOptions"
        optionLabel="label"
        optionValue="value"
        optionDisabled="disabled"
        :allowEmpty="false"
      />
      <FloatLabel style="flex-grow: 1; max-width: 250px">
        <IconField class="full">
          <InputIcon>
            <i class="pi pi-search" />
          </InputIcon>
          <InputText v-model="search" placeholder="Search" />
        </IconField>
      </FloatLabel>
    </div>
    <ScrollPanel style="width: 100%; height: 300px">
      <DataTable
        v-model:selection="selected"
        :value="targetCollections"
        dataKey="target_collection_id"
        :rowClass="(data) => (data._outOfScope ? 'out-of-scope-row' : '')"
        :isDataSelectable="(event) => !event.data._outOfScope"
      >
        <Column v-if="mode == 'targets'" selectionMode="multiple" headerStyle="width: 3rem" />
        <Column v-else selectionMode="single" headerStyle="width: 3rem" />
        <Column header="Name" field="target_collection_name">
          <template #body="{ data }">
            <span
              v-if="!data.workspace_id"
              v-tooltip.top="{ value: 'Global collection', showDelay: 500 }"
              class="pi ph ph-globe scope-icon scope-global"
            />
            <span
              v-else-if="data._outOfScope"
              v-tooltip.top="{ value: 'From another workspace', showDelay: 500 }"
              class="pi ph ph-lock scope-icon scope-locked"
            />
            <span>{{ data.target_collection_name }}</span>
          </template>
        </Column>
        <Column header="Description" field="target_collection_description" />
      </DataTable>
    </ScrollPanel>
  </Panel>
</template>

<style scoped>
.row {
  margin-bottom: 1rem;
  height: fit-content;
}

:deep(.p-floatlabel) {
  margin: 0;
}

.scope-icon {
  margin-right: 0.4rem;
  font-size: 0.85rem;
  opacity: 0.6;
}

.scope-global {
  color: var(--p-primary-color);
}

.scope-locked {
  color: var(--p-text-muted-color);
}

:deep(.out-of-scope-row) {
  opacity: 0.45;
  pointer-events: none;
}
</style>
