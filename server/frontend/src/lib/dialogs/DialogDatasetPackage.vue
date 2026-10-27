<script setup>
import { computed, ref, watch } from 'vue'

import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'

import BaseLoadError from '@/lib/base/BaseLoadError.vue'
import { metadataLines } from '@/lib/datasetTombstones'
import {
  batchesLabel,
  collectionLabel,
  exportPackage,
  gapEditable,
  loadPackagePreview,
  notKeptLabel,
  rawFilesLabel,
  samplesLabel
} from '@/lib/datasetPackage'

import DialogDatasetOp from './DialogDatasetOp.vue'

// "Prepare for publication": what a dataset's deposit package will hold, what
// the dataset still lacks for a deposit, and the archive itself. The gaps are
// filled in the dataset dialog, opened over this one, and the preview is read
// again when it closes. A gap does not stop the build: the archive says "not
// recorded" where a field is empty, as the operator command's does.

// The dataset row to prepare; the dialog is open while there is one.
const dataset = defineModel('dataset')

const preview = ref(null)
const error = ref(null)
const building = ref(false)
// The dataset dialog's action, 'edit' while it is open over this one. Escape
// then closes that dialog alone: every open PrimeVue Dialog listens for it on
// the document, the one underneath included.
const editing = ref(null)
let request = 0

const visible = computed({
  get: () => !!dataset.value,
  set: (shown) => {
    if (!shown) dataset.value = null
  }
})

const name = computed(() => preview.value?.dataset?.dataset_name ?? dataset.value?.dataset_name)
const gaps = computed(() => preview.value?.gaps ?? [])
const editable = computed(() => gaps.value.some(gapEditable))
const record = computed(() => metadataLines(preview.value?.dataset))

const load = async () => {
  // An answer for a dataset the dialog has since left is dropped.
  const current = ++request
  error.value = null
  try {
    const answer = await loadPackagePreview(dataset.value)
    if (current === request) preview.value = answer
  } catch (caught) {
    if (current === request) error.value = caught
  }
}

// Read the dataset each time the dialog opens: its metadata may have changed.
watch(
  () => dataset.value?.dataset_id,
  (id) => {
    preview.value = null
    if (id) load()
  },
  { immediate: true }
)

// The dataset dialog closed, saved or not: read what the package holds now.
watch(editing, (action, before) => {
  if (before && !action && dataset.value) load()
})

async function build() {
  building.value = true
  try {
    await exportPackage(dataset.value)
  } catch {
    // The request layer has already shown the user what went wrong; keep the
    // dialog open so they can try again.
    return
  } finally {
    building.value = false
  }
  dataset.value = null
}
</script>

<template>
  <Dialog
    v-model:visible="visible"
    :header="`Prepare '${name ?? ''}' for publication`"
    modal
    :closeOnEscape="!editing"
    style="width: min(48rem, 95vw)"
  >
    <p class="intro">
      One archive of the dataset to upload to a data repository such as Zenodo: its raw files, each
      batch's spreadsheet, the target collections, and a README and manifest that describe them. The
      repository keeps it and gives it a DOI.
    </p>
    <BaseLoadError
      v-if="error"
      :error="error"
      fallback="Could not read what the package would hold."
      :onRetry="load"
    />
    <ProgressSpinner v-else-if="!preview" />
    <template v-else>
      <Message v-if="gaps.length" severity="warn" class="gaps">
        <strong>Missing before a deposit</strong>
        <ul>
          <li v-for="gap in gaps" :key="gap.field">{{ gap.message }}</li>
        </ul>
        <Button
          v-if="editable"
          label="Edit dataset"
          icon="pi pi-pen-to-square"
          size="small"
          severity="secondary"
          @click="editing = 'edit'"
        />
      </Message>
      <Message v-else severity="success" class="gaps">
        The dataset has a licence, authors and a description: what a repository asks for.
      </Message>

      <h3>Metadata record</h3>
      <dl class="details">
        <dt>Name</dt>
        <dd>{{ preview.dataset.dataset_name }}</dd>
        <dt>Description</dt>
        <dd>{{ preview.dataset.dataset_description ?? 'Not recorded' }}</dd>
        <template v-for="(line, index) in record" :key="`${line.label}-${index}`">
          <dt>{{ line.label }}</dt>
          <dd>{{ line.value }}</dd>
        </template>
      </dl>

      <h3>Contents</h3>
      <dl class="details">
        <dt>Batches</dt>
        <dd>
          {{ batchesLabel(preview) }}
          <ul v-if="preview.batches.length" class="batches">
            <li v-for="batch in preview.batches" :key="batch.sample_batch_id">
              {{ batch.sample_batch_name }}: {{ samplesLabel(batch.samples) }}
            </li>
          </ul>
        </dd>
        <dt>Raw files</dt>
        <dd>{{ rawFilesLabel(preview) }}</dd>
        <template v-if="preview.raw_files_not_kept.length">
          <dt>Not kept</dt>
          <dd>
            {{ notKeptLabel(preview.raw_files_not_kept) }}. Mascope keeps only the data converted
            from them; the package describes them without their raw file.
          </dd>
        </template>
        <dt>Acquisition records</dt>
        <dd>{{ preview.acquisition_records }}</dd>
        <dt>Target collections</dt>
        <dd>{{ preview.target_collections.map(collectionLabel).join('; ') || 'None' }}</dd>
        <dt>Ionization modes</dt>
        <dd>
          {{ preview.ionization_modes.map((m) => m.ionization_mode_name).join(', ') || 'None' }}
        </dd>
        <dt>Archive</dt>
        <dd class="id">{{ preview.archive }}, {{ preview.files }} files</dd>
      </dl>
    </template>
    <menu>
      <Button label="Cancel" severity="secondary" @click="visible = false" />
      <Button
        label="Build package"
        icon="pi pi-download"
        :disabled="!preview || building"
        @click="build"
      />
    </menu>
  </Dialog>
  <DialogDatasetOp v-model:action="editing" :dataset="preview?.dataset" />
</template>

<style scoped>
.intro {
  margin-top: 0;
  opacity: 0.7;
}

.gaps ul {
  margin: 0.25rem 0 0.5rem;
  padding-left: 1.25rem;
}

h3 {
  margin: 1rem 0 0.4rem;
  font-size: 1rem;
}

.details {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.2rem 0.75rem;
  margin: 0;
}

.details dt {
  opacity: 0.6;
}

.details dd {
  margin: 0;
  overflow-wrap: anywhere;
}

.batches {
  max-height: 8rem;
  overflow: auto;
  margin: 0.2rem 0 0;
  padding-left: 1.25rem;
}

.id {
  font-family: 'IBM Plex Mono', monospace;
}

menu {
  margin-top: 1rem;
}
</style>
