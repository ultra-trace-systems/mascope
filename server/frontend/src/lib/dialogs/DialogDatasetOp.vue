<script setup>
import { ref, reactive, computed, watch } from 'vue'

import FloatLabel from 'primevue/floatlabel'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import Button from 'primevue/button'

import { useApp } from '@/stores'

import DatasetMetadataSection from './DatasetMetadataSection.vue'
import { metadataForm, metadataInvalid, metadataPayload } from './datasetMetadata.js'

const app = useApp()

const action = defineModel('action')

const props = defineProps({
  dataset: {
    type: Object
  }
})

const original = computed(() => {
  if (action.value == 'create') {
    return null
  }
  // Use the passed dataset prop if available, otherwise use the focused dataset
  return props.dataset ?? app.data.dataset.focused
})

const info = reactive({
  name: null,
  desc: null,
  message: null,
  initial: null,
  metadata: metadataForm(null)
})

// dialog visibility reactivity
const visible = ref(false)
watch(action, (value) => {
  visible.value = !!value
})
watch(visible, (value) => {
  if (!value) {
    action.value = null
  }
})

const title = computed(() => {
  const name = info.initial?.dataset_name ?? ''
  return {
    create: `Create a new dataset`,
    edit: `Edit dataset '${name}'`,
    delete: `Delete dataset '${name}'`
  }[action.value]
})

const executeLabel = computed(() => {
  return {
    create: `Create`,
    edit: `Save`,
    delete: `Delete`
  }[action.value]
})

async function execute() {
  try {
    switch (action.value) {
      /**
       * Handles the creation of a new dataset.
       * - After successfully creating the dataset, it sets up a one-time watcher.
       * - The watcher focuses on the newly created dataset once it is added to the dataset list.
       */
      case 'create': {
        const response = await app.data.dataset.create({
          dataset_name: info.name,
          dataset_description: info.desc,
          ...metadataPayload(info.metadata)
        })
        app.data.dataset.focusWhenPresent({
          dataset_id: response.data.dataset_id
        })
        break
      }
      case 'edit': {
        await app.data.dataset.update({
          dataset_id: original.value.dataset_id,
          // The server names acquisition datasets and refuses any update
          // carrying a name for one, unchanged or not
          ...(systemNamed.value ? {} : { dataset_name: info.name }),
          dataset_description: info.desc,
          ...metadataPayload(info.metadata)
        })
        break
      }
      /**
       * Handles the deletion of a dataset.
       * - Determines the next dataset to focus on (previous in the list or next one).
       * - Sets up a one-time watcher to focus on the new dataset after the current one is deleted.
       */
      case 'delete': {
        app.data.dataset.delete(original.value)
        break
      }
    }
  } catch {
    // The request layer has already shown the user what went wrong (a name
    // the workspace already uses comes back as a 409 toast). Keep the dialog
    // open on that so the name can be corrected - closing it would look like
    // the save succeeded.
    return
  }
  if (!info.message) {
    action.value = null
  }
}

// Initialize the dialog fields based on the selected action
watch(action, init)
function init() {
  info.name = original.value?.dataset_name
  info.desc = original.value?.dataset_description
  info.metadata = metadataForm(original.value)
  ;((info.message = null), (info.initial = original.value))
}

// Acquisition datasets are named by the server; their other fields, the
// descriptive metadata included, are edited like any dataset's.
const systemNamed = computed(
  () => action.value == 'edit' && original.value?.dataset_type == 'ACQUISITION'
)

const invalid = computed(
  () =>
    (action.value == 'create' && (info.name?.trim().length ?? 0) == 0) ||
    (action.value != 'delete' && metadataInvalid(info.metadata))
)
</script>

<template>
  <Dialog v-model:visible="visible" :header="title" modal style="width: 720px">
    <section>
      <template v-if="action !== 'delete'">
        <FloatLabel>
          <InputText
            id="dataset-name"
            v-model="info.name"
            :disabled="systemNamed"
            style="width: 100%"
          />
          <label for="dataset-name">Name</label>
        </FloatLabel>
        <FloatLabel>
          <Textarea id="dataset-desc" v-model="info.desc" rows="4" style="width: 100%" />
          <label for="dataset-desc">Description</label>
        </FloatLabel>
        <DatasetMetadataSection v-model="info.metadata" />
      </template>
      <template v-else>
        <p v-if="info.message">{{ info.message }}</p>
        <p v-else>
          Are you sure you want to delete the '{{ info.initial?.dataset_name }}' dataset?
        </p>
      </template>
    </section>
    <menu>
      <Button label="Cancel" @click="action = null" severity="secondary" />
      <Button :label="executeLabel" @click="execute" v-if="!info.message" :disabled="invalid" />
    </menu>
  </Dialog>
</template>
