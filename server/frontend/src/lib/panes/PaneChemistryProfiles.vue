<script setup>
/**
 * The chemistry profiles Mascope ships, for a regular user.
 *
 * Each row says it is shipped, what it calibrates its files on, and what it
 * has routed: how many files the server filed under it and when the newest
 * was acquired. Nothing here is edited. A profile is taken up by choosing it
 * for a file in Raw files (Choose chemistry); pointing one at a collection of
 * the server's own, and the server's own modes, tokens and mechanisms, are
 * under Advanced.
 */
import { computed } from 'vue'

import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'

import { useApp } from '@/stores'
import { calibrates, profiles, routedSummary } from '@/lib/chemistryProfiles'

const app = useApp()

const POLARITY_NAMES = { '-': 'Negative', '+': 'Positive' }

const collectionName = (id) =>
  app.data.target.collection.list.find((c) => c.target_collection_id === id)
    ?.target_collection_name ?? id

const rows = computed(() =>
  profiles(app.data.ionization.mode.list).map((mode) => ({
    id: mode.ionization_mode_id,
    name: mode.ionization_mode_name,
    polarity: POLARITY_NAMES[mode.ionization_mode_polarity] ?? mode.ionization_mode_polarity,
    calibrants: calibrates(mode) ? collectionName(mode.calibration_collection_id) : null,
    ...routedSummary(mode)
  }))
)
</script>

<template>
  <p class="intro">
    Mascope ships these chemistries, ready on every server. To process a file under one, right-click
    it in <strong>Raw files</strong> and choose <strong>Choose chemistry</strong>. A profile
    calibrates its files on the calibrants it ships; one with none is calibrated once it points at a
    calibrant collection, under <strong>Advanced</strong>.
  </p>
  <section class="list">
    <DataTable :value="rows" dataKey="id" scrollable scrollHeight="flex" size="small">
      <Column header="Profile">
        <template #body="{ data }">
          {{ data.name }}
          <Tag value="Shipped" severity="secondary" class="shipped" />
        </template>
      </Column>
      <Column header="Polarity" field="polarity" style="width: 110px" />
      <Column header="Calibrants">
        <template #body="{ data }">
          <span v-if="data.calibrants">{{ data.calibrants }}</span>
          <span
            v-else
            class="muted"
            v-tooltip="{
              value:
                'No reagent of its own ships with this chemistry. Point it at a calibrant collection under Advanced to calibrate its files.',
              showDelay: 300
            }"
            >None shipped</span
          >
        </template>
      </Column>
      <Column header="Files routed" field="files" style="width: 140px" />
      <Column header="Last file" style="width: 200px">
        <template #body="{ data }">
          <span v-if="data.last">{{ data.last }}</span>
          <span v-else class="muted">-</span>
        </template>
      </Column>
    </DataTable>
  </section>
</template>

<style scoped>
.intro {
  margin: 0 0 1rem;
  color: var(--p-text-muted-color);
}

/* The table takes the height the panel leaves and scrolls its own rows. */
.list {
  flex: 1;
  min-height: 7rem;
  display: flex;
  flex-flow: column;
}

.list :deep(.p-datatable) {
  flex: 1;
  min-height: 0;
}

.shipped {
  margin-left: 0.5rem;
  font-size: 0.7rem;
}

.muted {
  color: var(--p-text-muted-color);
}
</style>
