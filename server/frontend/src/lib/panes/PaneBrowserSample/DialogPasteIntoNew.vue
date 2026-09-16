<script setup>
import { computed } from 'vue'

import FloatLabel from 'primevue/floatlabel'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'

import { useApp } from '@/stores'

import { useClipboard, usePasteIntoNew } from './stores'

const app = useApp()
const clipboard = useClipboard()
const paste = usePasteIntoNew()

const what = computed(() => {
  if (paste.batchPaste) return `batch '${clipboard.batch.sample_batch_name}'`
  const count = clipboard.samples?.length ?? 0
  return `${count} sample${count === 1 ? '' : 's'}`
})

const into = computed(() => {
  if (paste.dialog.dataset && paste.dialog.batch) return 'dataset and batch'
  return paste.dialog.dataset ? 'dataset' : 'batch'
})

const where = computed(() =>
  paste.dialog.dataset
    ? `workspace '${app.data.workspace.focused?.workspace_name}'`
    : `dataset '${app.data.dataset.focused?.dataset_name}'`
)
</script>

<template>
  <Dialog
    v-model:visible="paste.dialog.visible"
    :header="`Paste into a new ${into}`"
    modal
    style="width: 520px"
  >
    <form @submit.prevent="paste.execute">
      <p>
        {{ clipboard.op === 'cut' ? 'Moves' : 'Copies' }} {{ what }} into a new {{ into }} in
        {{ where }}.
      </p>
      <FloatLabel v-if="paste.dialog.dataset">
        <InputText
          id="paste-new-dataset-name"
          v-model="paste.dialog.datasetName"
          :disabled="!!paste.dialog.createdDatasetId"
          autofocus
          style="width: 100%"
        />
        <label for="paste-new-dataset-name">Dataset name</label>
      </FloatLabel>
      <FloatLabel v-if="paste.dialog.batch">
        <InputText
          id="paste-new-batch-name"
          v-model="paste.dialog.batchName"
          :disabled="!!paste.dialog.createdBatchId"
          :autofocus="!paste.dialog.dataset"
          style="width: 100%"
        />
        <label for="paste-new-batch-name">
          Batch name{{ paste.dialog.dataset ? ' (defaults to the dataset name)' : '' }}
        </label>
      </FloatLabel>
      <menu>
        <Button
          label="Cancel"
          severity="secondary"
          type="button"
          @click="paste.dialog.visible = false"
        />
        <Button
          label="Paste"
          icon="pi pi-clipboard"
          type="submit"
          :disabled="paste.invalid"
          :loading="paste.dialog.pending"
        />
      </menu>
    </form>
  </Dialog>
</template>
