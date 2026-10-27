<script setup>
import { ref, watch } from 'vue'

import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import ProgressSpinner from 'primevue/progressspinner'

// From their files, not the base index: the index loads components that read
// the runtime config on import, which this dialog (and its spec) need not pull in.
import BaseEmptyState from '@/lib/base/BaseEmptyState.vue'
import BaseLoadError from '@/lib/base/BaseLoadError.vue'
import {
  REFERENCE_LISTS_GUIDE_URL,
  loadedLabel,
  polarityLabel,
  windowLabel
} from '@/lib/referenceSources'
import { useReference } from '@/stores/data/modules/reference'

import ReferenceSpecies from './ReferenceSpecies.vue'

// The reference lists this deployment has loaded, read only, opened from
// Settings: one row per active source, and its species a page at a time when
// a row is opened. Lists are the deployment's, so every signed-in user sees
// the same rows. A list the deployment's licence gate does not admit is shown
// all the same, with a note that peak assignment does not match against it.

const visible = defineModel('visible')

const reference = useReference()
const expandedRows = ref({})

// Read the list each time the dialog opens: an operator loads a list on the
// server, which tells no open page.
watch(
  visible,
  (shown) => {
    if (shown) reference.load('dialog')
  },
  { immediate: true }
)
</script>

<template>
  <Dialog
    v-model:visible="visible"
    header="Reference lists"
    modal
    dismissableMask
    style="width: min(80rem, 95vw)"
  >
    <p class="intro">
      The compound lists this deployment has loaded. Peak assignment names a formula after the lists
      that hold it.
    </p>
    <BaseLoadError
      v-if="reference.error"
      :error="reference.error"
      fallback="Could not load the reference lists."
      :onRetry="() => reference.load('retry')"
    />
    <ProgressSpinner v-else-if="reference.pending && !reference.list.length" />
    <BaseEmptyState
      v-else-if="!reference.list.length"
      icon="pi ph ph-books"
      title="No reference lists loaded"
      hint="An operator loads them on the server, the lists Mascope ships with one command."
    >
      <template #actions>
        <Button
          as="a"
          :href="REFERENCE_LISTS_GUIDE_URL"
          target="_blank"
          rel="noopener noreferrer"
          label="How to load a list"
          icon="pi pi-external-link"
          size="small"
          severity="secondary"
          text
        />
      </template>
    </BaseEmptyState>
    <DataTable
      v-else
      :value="reference.list"
      dataKey="name"
      v-model:expandedRows="expandedRows"
      scrollable
      scrollHeight="60vh"
      size="small"
    >
      <Column expander style="width: 3rem" />
      <Column header="List">
        <template #body="{ data }">
          <div class="label">{{ data.label }}</div>
          <div v-if="data.label !== data.name" class="muted name">{{ data.name }}</div>
        </template>
      </Column>
      <Column header="Version">
        <template #body="{ data }"
          ><span class="nowrap">{{ data.version }}</span></template
        >
      </Column>
      <Column header="Licence">
        <template #body="{ data }">
          <div class="nowrap">{{ data.license ?? '-' }}</div>
          <div v-if="!data.admitted" class="gated">
            <span class="pi pi-ban" aria-hidden="true" />
            Not matched here: this deployment's licence settings leave it out.
          </div>
        </template>
      </Column>
      <Column header="Polarity">
        <template #body="{ data }">{{ polarityLabel(data.polarity) }}</template>
      </Column>
      <Column header="Radicals">
        <template #body="{ data }">{{ data.allow_radicals ? 'Allowed' : 'No' }}</template>
      </Column>
      <Column header="Window">
        <template #body="{ data }">{{ windowLabel(data.known_window) }}</template>
      </Column>
      <Column header="Species">
        <template #body="{ data }">{{ data.record_count }}</template>
      </Column>
      <Column header="Loaded">
        <template #body="{ data }">
          <span class="nowrap">{{ loadedLabel(data.ingested_at) }}</span>
        </template>
      </Column>
      <template #expansion="{ data }">
        <ReferenceSpecies :source="data.name" :version="data.version" />
      </template>
    </DataTable>
  </Dialog>
</template>

<style scoped>
.intro {
  margin-top: 0;
  opacity: 0.7;
}

.muted {
  opacity: 0.6;
}

.nowrap {
  white-space: nowrap;
}

.label {
  font-weight: 600;
}

.name {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.85rem;
}

.gated {
  margin-top: 0.2rem;
  font-size: 0.85rem;
  color: var(--p-orange-500);
}
</style>
