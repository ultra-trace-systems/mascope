<script setup>
import { ref, reactive, computed, watch } from 'vue'

import FloatLabel from 'primevue/floatlabel'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Panel from 'primevue/panel'
import InputText from 'primevue/inputtext'
import Tabs from 'primevue/tabs'
import TabList from 'primevue/tablist'
import Tab from 'primevue/tab'
import TabPanels from 'primevue/tabpanels'
import TabPanel from 'primevue/tabpanel'
import TextArea from 'primevue/textarea'

import { api } from '@/api'

import { equals } from '@/lib/table'
import { DEFAULT_SAMPLE_BATCH_TYPE, ANALYSIS_POLARITY } from '@/lib/constants'
import { clone } from '@/lib/utils'

import { useApp } from '@/stores'

import ChemistryContextSelect from '../ChemistryContextSelect.vue'
import PaneSelectTargets from './PaneSelectTargets.vue'

const app = useApp()
const layer = 'dialog_batch_op' // Help-mode layer for dialog

const props = defineProps({
  batch: {
    type: Object
  }
})

const original = computed(() => props.batch ?? app.data.batch.focused)

// api
const action = defineModel('action') // create, update, update_targets
const ready = ref(false)

// dialog visibility reactivity
const visible = ref(false)
watch([action, ready], () => {
  visible.value = !!action.value && ready.value
})
watch(visible, (value) => {
  if (!value) {
    action.value = null
    app.ui.help.set(null)
  } else {
    app.ui.help.set(layer)
  }
})

// reactive
const tab = ref('info')
const initial = reactive({
  info: {
    name: '',
    desc: '',
    type: '',
    polarity: '',
    context: null
  }
})
const selected = reactive({
  info: {
    name: '',
    desc: '',
    type: '',
    polarity: '',
    // The chemistry context the batch names for its samples; null for none.
    context: null
  },
  targets: [] // multiple target collections
})

// computed
const title = computed(
  () =>
    ({
      create: `Create a new sample batch`,
      update: `Edit sample batch "${selected.info.name}"`,
      update_targets: `Edit collections of sample batch "${selected.info.name}"`
    })[action.value]
)
const updated = computed(() => {
  const common = {
    sample_batch_name: selected.info.name,
    sample_batch_description: selected.info.desc,
    sample_batch_polarity: selected.info.polarity,
    chemistry_context: selected.info.context,
    dataset_id: app.data.dataset.focusedId,
    target_collection_ids: selected.targets.map((row) => row.target_collection_id)
  }
  switch (action.value) {
    case 'create': {
      return common
    }
    case 'update':
    case 'update_targets': {
      return {
        ...common,
        sample_batch_id: original.value.sample_batch_id,
        sample_batch_utc_created: original.value.sample_batch_utc_created
      }
    }
    default:
      return null
  }
})
const changed = computed(() =>
  ready.value
    ? selected.info.name !== initial.info.name ||
      selected.info.desc !== initial.info.desc ||
      selected.info.context !== initial.info.context ||
      !equals(selected.targets, initial.targets, 'target_collection_id')
    : false
)
const invalid = computed(() => {
  switch (action.value) {
    case 'create': {
      return !selected.info.name
    }
    case 'update': {
      const infoValid = selected.info.name.length > 0
      return !changed.value || !infoValid
    }
    case 'update_targets': {
      return equals(initial.targets, selected.targets, 'target_collection_id')
    }
    default:
      return false
  }
})

// initialization
watch(action, init)
async function init(value) {
  if (!value) return
  ready.value = true
  const modification = ['update', 'update_targets'].includes(action.value)
  if (modification) {
    // init information
    selected.info.name = original.value.sample_batch_name
    selected.info.desc = original.value.sample_batch_description
    selected.info.type = original.value.sample_batch_type
    selected.info.polarity = original.value.polarity
    selected.info.context = original.value.chemistry_context ?? null
    // init target collections with batch collections
    const batchCollections = (
      await api.http.get(`/sample/batches/${original.value.sample_batch_id}/targets`, {
        use: 'read',
        type: 'read_batch_collections'
      })
    )?.target_collections
    selected.targets = app.data.target.collection.list.filter((coll) =>
      batchCollections
        .map(({ target_collection_id }) => target_collection_id)
        .includes(coll.target_collection_id)
    )
  } else {
    // init information
    selected.info.name = ''
    selected.info.desc = ''
    selected.info.type = DEFAULT_SAMPLE_BATCH_TYPE // Set default type
    selected.info.polarity = ANALYSIS_POLARITY // Default polarity for ANALYSIS batches
    selected.info.context = null
    // init target collections with defaults
    selected.targets = []
  }
  // save initial state
  initial.info = clone(selected.info)
  initial.targets = clone(selected.targets)
  // set initial tab
  tab.value = value == 'update_targets' ? 'targets' : 'info'
}

// confirmation
async function execute() {
  action.value = null
  switch (action.value) {
    case 'create': {
      app.data.batch.create(updated.value)
      break
    }
    case 'update':
    case 'update_targets': {
      await app.data.batch.update(updated.value)
      break
    }
  }
}
</script>

<template>
  <Dialog
    v-model:visible="visible"
    :header="title"
    style="min-width: 800px; min-height: 600px"
    contentStyle="flex-grow: 1; display: flex; flex-flow: column; gap: 0.5rem; justify-content: space-between"
  >
    <Tabs v-model:value="tab">
      <TabList>
        <Tab value="info">Info</Tab>
        <Tab value="targets" :disabled="action == 'update'">Targets</Tab>
      </TabList>
      <TabPanels>
        <TabPanel value="info">
          <Panel style="text-align: center">
            <FloatLabel>
              <InputText
                id="batch-name"
                v-model="selected.info.name"
                :disabled="action == 'update_targets'"
                style="width: 65ch"
              />
              <label for="batch-name">Name</label>
            </FloatLabel>
            <FloatLabel>
              <TextArea
                id="batch-desc"
                v-model="selected.info.desc"
                :disabled="action == 'update_targets'"
                autoResize
                style="width: 65ch"
              />
              <label for="batch-desc">Description</label>
            </FloatLabel>
            <div style="width: 65ch; margin: 0 auto">
              <ChemistryContextSelect
                id="batch-context"
                v-model="selected.info.context"
                owner="batch"
                :disabled="action == 'update_targets'"
              />
            </div>
          </Panel>
        </TabPanel>

        <TabPanel value="targets">
          <PaneSelectTargets
            mode="targets"
            :batch="selected.info"
            v-model:selected="selected.targets"
          />
        </TabPanel>
      </TabPanels>
    </Tabs>

    <menu>
      <Button label="Cancel" @click="action = null" icon="pi pi-times" severity="secondary" />
      <Button label="Save" @click="execute" icon="pi pi-save" :disabled="invalid" />
    </menu>
  </Dialog>
</template>
