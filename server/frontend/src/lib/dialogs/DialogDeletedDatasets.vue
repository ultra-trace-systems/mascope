<script setup>
import { computed, ref, watch } from 'vue'

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
  TOMBSTONE_PAGE_SIZE,
  countsLabel,
  deletedLabel,
  loadTombstones,
  metadataLines,
  whenLabel
} from '@/lib/datasetTombstones'

// What each deleted dataset was, read only, opened from Settings. The server
// decides which rows a user sees - their workspaces' for a member, every one
// for a global admin or owner, those of deleted workspaces included - so the
// dialog lists what it is given, a page at a time, newest delete first.

const visible = defineModel('visible')

const rows = ref([])
const total = ref(0)
const page = ref(0)
const loading = ref(false)
const error = ref(null)
const expandedRows = ref({})
let request = 0

const pages = computed(() => Math.max(1, Math.ceil(total.value / TOMBSTONE_PAGE_SIZE)))
const range = computed(() => {
  const first = page.value * TOMBSTONE_PAGE_SIZE + 1
  const last = first + rows.value.length - 1
  return `${first}-${last} of ${total.value}`
})

const load = async (to = page.value) => {
  const current = ++request
  loading.value = true
  error.value = null
  try {
    const answer = await loadTombstones(to)
    if (current !== request) return
    rows.value = answer.data
    total.value = answer.total
    page.value = to
  } catch (caught) {
    if (current === request) error.value = caught
  } finally {
    if (current === request) loading.value = false
  }
}

// Read the newest page each time the dialog opens: a dataset may have been
// deleted since, in this tab or another.
watch(
  visible,
  (shown) => {
    if (shown) load(0)
  },
  { immediate: true }
)
</script>

<template>
  <Dialog
    v-model:visible="visible"
    header="Deleted datasets"
    modal
    dismissableMask
    style="width: min(60rem, 95vw)"
  >
    <p class="intro">
      Deleting a dataset removes its data but keeps a record of what it was. Newest first.
    </p>
    <BaseLoadError
      v-if="error"
      :error="error"
      fallback="Could not load the deleted datasets."
      :onRetry="() => load()"
    />
    <ProgressSpinner v-else-if="loading && !rows.length" />
    <BaseEmptyState
      v-else-if="!rows.length"
      icon="pi ph ph-clock-counter-clockwise"
      title="No deleted datasets"
      hint="A record of each dataset deleted in your workspaces shows up here."
    />
    <template v-else>
      <DataTable
        :value="rows"
        dataKey="dataset_id"
        v-model:expandedRows="expandedRows"
        scrollable
        scrollHeight="60vh"
        size="small"
      >
        <Column expander style="width: 3rem" />
        <Column header="Dataset">
          <template #body="{ data }">{{ data.dataset_name ?? data.dataset_id }}</template>
        </Column>
        <Column header="Workspace">
          <template #body="{ data }">{{ data.workspace_name ?? '-' }}</template>
        </Column>
        <Column header="Deleted">
          <template #body="{ data }">{{ deletedLabel(data) }}</template>
        </Column>
        <Column header="Held">
          <template #body="{ data }">{{ countsLabel(data) }}</template>
        </Column>
        <template #expansion="{ data }">
          <dl class="details">
            <template v-if="data.dataset_description">
              <dt>Description</dt>
              <dd>{{ data.dataset_description }}</dd>
            </template>
            <template v-if="data.dataset_type">
              <dt>Type</dt>
              <dd>{{ data.dataset_type }}</dd>
            </template>
            <template v-if="data.instrument">
              <dt>Instrument</dt>
              <dd>{{ data.instrument }}</dd>
            </template>
            <dt>Created</dt>
            <dd>{{ whenLabel(data.dataset_utc_created) }}</dd>
            <dt>Modified</dt>
            <dd>{{ whenLabel(data.dataset_utc_modified) }}</dd>
            <template
              v-for="(line, index) in metadataLines(data.descriptive_metadata)"
              :key="`${line.label}-${index}`"
            >
              <dt>{{ line.label }}</dt>
              <dd>{{ line.value }}</dd>
            </template>
            <dt>Dataset id</dt>
            <dd class="id">{{ data.dataset_id }}</dd>
          </dl>
        </template>
      </DataTable>
      <div v-if="pages > 1" class="pager">
        <Button
          icon="pi pi-arrow-left"
          aria-label="Previous page"
          severity="secondary"
          text
          :disabled="loading || page === 0"
          @click="load(page - 1)"
        />
        <span class="muted">{{ range }}</span>
        <Button
          icon="pi pi-arrow-right"
          aria-label="Next page"
          severity="secondary"
          text
          :disabled="loading || page >= pages - 1"
          @click="load(page + 1)"
        />
      </div>
    </template>
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

.details {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.2rem 0.75rem;
  margin: 0.25rem 0 0.25rem 3rem;
}

.details dt {
  opacity: 0.6;
}

.details dd {
  margin: 0;
  overflow-wrap: anywhere;
}

.id {
  font-family: 'IBM Plex Mono', monospace;
  user-select: all;
}

.pager {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.5rem;
}
</style>
