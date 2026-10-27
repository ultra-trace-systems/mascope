<script setup>
import { computed, onMounted, ref } from 'vue'

import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import ProgressSpinner from 'primevue/progressspinner'

import BaseLoadError from '@/lib/base/BaseLoadError.vue'
import { SPECIES_PAGE_SIZE, referenceUrl } from '@/lib/referenceSources'
import { useReference } from '@/stores/data/modules/reference'

// The species one source holds, a page at a time, by mass: what a row of the
// Reference lists dialog shows when it is opened. It reads the version the
// row shows, so a list reloaded while it is open does not shift its pages.

const props = defineProps({
  source: { type: String, required: true },
  version: { type: String, required: false, default: null }
})

const reference = useReference()

const rows = ref([])
const total = ref(0)
const page = ref(0)
const loading = ref(false)
const error = ref(null)

const pages = computed(() => Math.max(1, Math.ceil(total.value / SPECIES_PAGE_SIZE)))
const range = computed(() => {
  const first = page.value * SPECIES_PAGE_SIZE + 1
  const last = first + rows.value.length - 1
  return `${first}-${last} of ${total.value}`
})

// One read at a time: the pager is disabled while one runs, and the retry
// shows only after one failed, so no older read can answer over a newer one.
const load = async (to = page.value) => {
  loading.value = true
  error.value = null
  try {
    const answer = await reference.species(props.source, { version: props.version, page: to })
    rows.value = answer.data
    total.value = answer.total
    page.value = to
  } catch (caught) {
    error.value = caught
  } finally {
    loading.value = false
  }
}

onMounted(() => load(0))
</script>

<template>
  <div class="species">
    <BaseLoadError
      v-if="error"
      :error="error"
      fallback="Could not load the species of this list."
      :onRetry="() => load()"
    />
    <ProgressSpinner v-else-if="loading && !rows.length" />
    <p v-else-if="!rows.length" class="muted">This list holds no species.</p>
    <template v-else>
      <DataTable :value="rows" size="small" class="species-table">
        <Column header="Name">
          <template #body="{ data }">{{ data.name ?? '-' }}</template>
        </Column>
        <Column header="Formula" field="formula" />
        <Column header="Mass">
          <template #body="{ data }">{{ data.monoisotopic_mass?.toFixed(4) ?? '-' }}</template>
        </Column>
        <Column header="Reference">
          <template #body="{ data }">
            <a
              v-if="referenceUrl(data.reference)"
              :href="referenceUrl(data.reference)"
              target="_blank"
              rel="noopener noreferrer"
              >{{ data.reference }}</a
            >
            <template v-else>{{ data.reference ?? '-' }}</template>
          </template>
        </Column>
      </DataTable>
      <div v-if="pages > 1" class="pager">
        <Button
          icon="pi pi-arrow-left"
          aria-label="Previous species page"
          severity="secondary"
          text
          :disabled="loading || page === 0"
          @click="load(page - 1)"
        />
        <span class="muted">{{ range }}</span>
        <Button
          icon="pi pi-arrow-right"
          aria-label="Next species page"
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
.species {
  margin: 0.25rem 0 0.25rem 3rem;
}

.muted {
  opacity: 0.6;
}

.pager {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.5rem;
}
</style>
