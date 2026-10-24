<script setup>
import { computed, ref, watch, watchEffect } from 'vue'

import Button from 'primevue/button'
import ProgressSpinner from 'primevue/progressspinner'

// From their files, not the base index: the index loads components that read
// the runtime config on import, which this pane (and its spec) need not pull in.
import BaseEmptyState from '@/lib/base/BaseEmptyState.vue'
import BaseLoadError from '@/lib/base/BaseLoadError.vue'
import {
  TOMBSTONE_PAGE_SIZE,
  countsLabel,
  deletedViaLabel,
  loadTombstones,
  metadataLines,
  whenLabel
} from '@/lib/datasetTombstones'
import { useApp } from '@/stores'

import { useSidebarMenu } from './state.js'

// The home menu's Deleted datasets tab: what each deleted dataset was, read
// only. The server decides which rows a user sees - their workspaces' for a
// member, every one for a global admin or owner, those of deleted workspaces
// included - so the pane lists what it is given, a page at a time, newest
// delete first.

const app = useApp()
const sidebarMenu = useSidebarMenu()
const open = computed(() => sidebarMenu.open && sidebarMenu.tab === 'deleted')

const rows = ref([])
const total = ref(0)
const page = ref(0)
const loading = ref(false)
const error = ref(null)
const expanded = ref(new Set())
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

// Read again each time the tab is shown: a dataset may have been deleted
// since, in this tab or another.
watch(
  open,
  (shown) => {
    if (shown) load(0)
  },
  { immediate: true }
)

const toggle = (id) => {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}

const layer = 'sidebar_deleted_tab'
watchEffect(() => {
  if (open.value) {
    app.ui.help.set(layer)
  }
})

const vHelpLayer = app.ui.help.directive(layer)
</script>

<template>
  <div
    class="deleted"
    v-help-layer.right="
      `
      <b>Deleted datasets</b>
      <p>
        Deleting a dataset removes its data but keeps a record of what it was:
        its name and workspace, who deleted it and when, what it held and how it
        was described.
      </p>
      <p>
        You see the records of your workspaces' datasets. Administrators see
        every record, those of deleted workspaces included.
      </p>
      `
    "
  >
    <div class="row" style="align-items: center">
      <h2>Deleted datasets</h2>
      <Button
        icon="pi ph ph-arrow-clockwise"
        aria-label="Reload deleted datasets"
        v-tooltip.bottom="'Reload'"
        severity="secondary"
        text
        :disabled="loading"
        @click="load()"
      />
    </div>

    <BaseLoadError
      v-if="error"
      :error="error"
      fallback="Could not load the deleted datasets."
      :onRetry="() => load()"
    />
    <ProgressSpinner v-else-if="loading && !rows.length" />
    <BaseEmptyState
      v-else-if="!rows.length"
      icon="pi ph ph-trash"
      title="No deleted datasets"
      hint="A record of each dataset deleted in your workspaces shows up here."
    />
    <template v-else>
      <ul class="tombstones">
        <li v-for="row in rows" :key="row.dataset_id" class="tombstone">
          <div class="head">
            <div class="title">
              <span class="name">{{ row.dataset_name ?? row.dataset_id }}</span>
              <span class="muted">in {{ row.workspace_name ?? 'an unnamed workspace' }}</span>
            </div>
            <Button
              :icon="`pi ph ${expanded.has(row.dataset_id) ? 'ph-caret-up' : 'ph-caret-down'}`"
              :aria-label="`${expanded.has(row.dataset_id) ? 'Hide' : 'Show'} details of ${row.dataset_name ?? row.dataset_id}`"
              :aria-expanded="expanded.has(row.dataset_id)"
              severity="secondary"
              text
              size="small"
              @click="toggle(row.dataset_id)"
            />
          </div>
          <span class="deleted-line">
            Deleted {{ deletedViaLabel(row) }} on {{ whenLabel(row.deleted_utc) }}
            <template v-if="row.deleted_by">by {{ row.deleted_by }}</template>
          </span>
          <span class="muted">Held {{ countsLabel(row) }}</span>
          <dl v-if="expanded.has(row.dataset_id)" class="details">
            <template v-if="row.dataset_description">
              <dt>Description</dt>
              <dd>{{ row.dataset_description }}</dd>
            </template>
            <template v-if="row.dataset_type">
              <dt>Type</dt>
              <dd>{{ row.dataset_type }}</dd>
            </template>
            <template v-if="row.instrument">
              <dt>Instrument</dt>
              <dd>{{ row.instrument }}</dd>
            </template>
            <dt>Created</dt>
            <dd>{{ whenLabel(row.dataset_utc_created) }}</dd>
            <dt>Modified</dt>
            <dd>{{ whenLabel(row.dataset_utc_modified) }}</dd>
            <template
              v-for="(line, index) in metadataLines(row.descriptive_metadata)"
              :key="`${line.label}-${index}`"
            >
              <dt>{{ line.label }}</dt>
              <dd>{{ line.value }}</dd>
            </template>
            <dt>Dataset id</dt>
            <dd class="id">{{ row.dataset_id }}</dd>
          </dl>
        </li>
      </ul>
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
  </div>
</template>

<style scoped>
.deleted {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

h2 {
  margin: 0;
}

.tombstones {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  max-height: calc(100vh - 300px);
  overflow-y: auto;
}

.tombstone {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--p-drawer-border-color);
  border-radius: 0.5rem;
}

.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 0.5rem;
}

.title {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  min-width: 0;
}

.name {
  font-weight: 600;
  overflow-wrap: anywhere;
}

.muted {
  opacity: 0.6;
}

.details {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.2rem 0.75rem;
  margin: 0.5rem 0 0;
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
}
</style>
