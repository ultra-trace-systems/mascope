<script setup>
import { computed } from 'vue'

import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import ProgressSpinner from 'primevue/progressspinner'

import { BaseMatchTag, BaseCopyableField } from '@/lib/base'
import { num } from '@/lib/formatters'
import { formatIsotopeFormula, monoisotopicIsotope } from '@/lib/chem'

import { useApp } from '@/stores'

const app = useApp()

// --- Computed ---
const ionFormula = () => app.data.match.visualized.ion?.target_ion_formula
const loading = computed(() => app.data.match.visualized.isotopes === null)

// The isotope the table opens on: the ion's monoisotopic isotopologue, which the
// ion formula names for a labelled ion. The rows arrive in the backend's m/z
// order, where the lightest one comes first - the M0 of an unlabelled ion, but
// for a labelled one the reagent's unlabelled remainder, a couple of percent of
// the line the ion is measured by.
const mainIsotopeId = computed(
  () => monoisotopicIsotope(app.data.match.visualized.isotopes, ionFormula())?.target_isotope_id
)

// The M0 row shows as the one the table opens on.
const rowClass = (data) =>
  data?.target_isotope_id === mainIsotopeId.value ? 'main-isotope-row' : ''

// Compute UI-based match category for display
const uiMatchCategory = (match) => {
  if (!match) return
  return app.data.match.params.uiCategory(match)
}
</script>

<template>
  <div class="isotope-table-container">
    <!-- Loading spinner -->
    <div v-if="loading">
      <ProgressSpinner strokeWidth="4" style="width: 2rem; height: 2rem" />
    </div>

    <!-- No data message -->
    <div v-else-if="!app.data.match.visualized.isotopes?.length">
      No matched isotopes found for {{ ionFormula() }}
    </div>

    <!-- Isotope data table -->
    <DataTable
      v-else
      :value="app.data.match.visualized.isotopes"
      dataKey="target_isotope_id"
      selectionMode="single"
      v-model:selection="app.data.match.visualized.isotopeSelected"
      size="small"
      :rowClass="rowClass"
      sortField="formula"
      :sortOrder="-1"
      scrollable
      scrollHeight="flex"
    >
      <!-- Match Score Column -->
      <Column class="match-column">
        <template #header>
          <span class="pi ph ph-seal-percent" />
        </template>
        <template #body="{ data }">
          <!-- Only real matches (possible/probable, category >= 1) show a tag.
               Isotopes that are not a match under the current tolerances - no
               detected peak, or a peak scored 0 - show none instead of a 0%. -->
          <BaseMatchTag
            v-if="uiMatchCategory(data.match) >= 1"
            :match-score="data.match?.match_score"
            :match-category="uiMatchCategory(data.match)"
            :alarming="data.match?.alarming"
            :tooltip="
              data.match?.sample_peak_intensity
                ? `Peak intensity: ${num.peakIntensity.format(data.match.sample_peak_intensity)} (cps)`
                : 'No peak intensity data'
            "
          />
        </template>
      </Column>

      <!-- formula Column: counted from the ion's M0, which the ion formula
           names for a labelled ion (see formatIsotopeFormula) -->
      <Column header="Substitution" field="formula" style="width: 8rem">
        <template #body="{ data }">
          <span v-tooltip="data.target_isotope_formula">
            {{ formatIsotopeFormula(data.target_isotope_formula, ionFormula()) }}
          </span>
        </template>
      </Column>

      <!-- m/z Column -->
      <Column header="m/z" field="mz" style="width: 8rem">
        <template #body="{ data }">
          <BaseCopyableField :field="num.mz.format(data.mz)" />
        </template>
      </Column>

      <!-- Relative Abundance Column -->
      <Column header="r.a." field="relative_abundance" style="width: 8rem">
        <template #body="{ data }">
          <BaseCopyableField :field="num.relativeAbundance.format(data.relative_abundance)" />
        </template>
      </Column>
    </DataTable>
  </div>
</template>

<style scoped>
.isotope-table-container {
  flex-shrink: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
  padding: 0rem;
  width: 40%;
  min-width: 300px;
  max-width: 500px;
}

.isotope-table-container :deep(.p-datatable) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.isotope-table-container :deep(.p-datatable-table-container) {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

/* Make the ion's M0 row appear selected (`rowClass`) */
.isotope-table-container :deep(.p-datatable tbody > tr.main-isotope-row) {
  background-color: var(--p-datatable-row-selected-background) !important;
}
</style>
