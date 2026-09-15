<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Panel from 'primevue/panel'
import ProgressSpinner from 'primevue/progressspinner'
import TabMenu from 'primevue/tabmenu'

import { num } from '@/lib/formatters'
import { BaseLoadError, BaseTierTag } from '@/lib/base'
import { peakAssignmentEnabled } from '@/lib/features'
import { useApp } from '@/stores'
import { usePeakScroller } from './stores'

// Two ledgers share this pane. With peak-centric assignment on it reports the
// committed assignment of the focused run. With it off it stays the ledger it
// has always been: m/z, height, area and the clickable target-match formulas,
// which are the only route from a peak to the Match tab in that build (the
// assignment browser is unreachable there).

const app = useApp()
const peakTable = ref(null)
const scroller = usePeakScroller()

defineProps({
  height: {
    type: Number,
    required: true
  }
})

// The per-peak assignments of the focused run, joined to peaks by
// String(peak_id) === sample_peak_id (see docs/dev/peak_assignment_frontend.md).
const assignments = computed(() => app.data.peakAssignment.peak)
const hasRun = computed(() => peakAssignmentEnabled && !!assignments.value.run)
const tierCounts = computed(() => assignments.value.tierCounts)

// Legacy counter: how many peaks carry at least one target match.
const matchedCount = computed(() => app.data.peak.list.filter((p) => p.match.length > 0).length)

const assignmentFor = (peak) => assignments.value.forPeak(peak?.peak_id)

// Source badge shown next to the committed formula.
const sourceIcon = (source) => {
  switch (source) {
    case 'database':
      return 'pi ph ph-database'
    case 'untargeted':
      return 'pi ph ph-magnifying-glass'
    default:
      return null
  }
}

// Watch for table ref to become available and bind to scroller
watch(
  peakTable,
  (newTableRef) => {
    if (newTableRef) {
      scroller.bind(newTableRef, () => app.data.peak.list)
    }
  },
  { immediate: true }
)

// Watch for focused peak and scroll to it
watch(
  () => app.data.peak.focusedId,
  (peakId) => {
    if (peakId) {
      scroller.scrollToPeak(peakId)
    }
  }
)
// Watch for changes in the peak list and scroll to focused peak if needed
// (after refreshing data)
watch(
  () => app.data.peak.list,
  () => {
    if (app.data.peak.focusedId) {
      scroller.scrollToPeak(app.data.peak.focusedId)
    }
  }
)

onBeforeUnmount(() => {
  scroller.bind(null, () => [])
})
</script>

<template>
  <Panel
    class="browser"
    style="border: none; min-width: 280px; max-width: 400px; width: 100%"
    :pt="
      app.ui.help.top(
        peakAssignmentEnabled
          ? `
        <h1>Peak Browser</h1>

        <p>
        Every detected peak in the selected sample and its committed assignment - formula,
        confidence tier and fit score - from the latest assignment run. Click a peak to inspect it.
        </p>
      `
          : `
        <h1>Peak Browser</h1>

        <p>
        List of detected peaks in the currently selected sample. Click on a peak to assign a composition.
        </p>
      `,
        { doc: app.ui.help.docUrl('how-it-works/peak-detection/') }
      )
    "
  >
    <template #header>
      <TabMenu :model="[{ label: 'Peaks', icon: 'pi ph ph-crosshair' }]" style="overflow: hidden" />
    </template>
    <template #icons>
      <span v-if="!peakAssignmentEnabled" style="opacity: 0.5"
        >{{ matchedCount }}/{{ app.data.peak.list.length }} peaks matched
      </span>
      <span v-else-if="hasRun" class="tier-summary">
        <span class="tier-stat assigned" v-tooltip.bottom="'Assigned'">
          {{ tierCounts.assigned }}
        </span>
        <span class="tier-stat candidate" v-tooltip.bottom="'Candidate'">
          {{ tierCounts.candidate }}
        </span>
        <span class="tier-stat below" v-tooltip.bottom="'Below assignability'">
          {{ tierCounts.below_assignability }}
        </span>
        <span class="tier-stat unassigned" v-tooltip.bottom="'Unassigned'">
          {{ tierCounts.unassigned }}
        </span>
      </span>
      <span v-else style="opacity: 0.5">{{ app.data.peak.list.length }} peaks &middot; no run</span>
    </template>
    <DataTable
      v-if="!app.data.peak.pending && !app.data.peak.error"
      ref="peakTable"
      :value="app.data.peak.list"
      dataKey="peak_id"
      selectionMode="single"
      :metaKeySelection="false"
      v-model:selection="app.data.peak.focused"
      :sortField="scroller.sortField"
      :sortOrder="scroller.sortOrder"
      @sort="(e) => scroller.setSort(e.sortField, e.sortOrder)"
      size="small"
      scrollable
      :scrollHeight="`${height}px`"
      :virtualScrollerOptions="{ itemSize: 35.5 }"
      :pt="{ bodyRow: ({ context }) => ({ id: app.data.peak.list[context.index]?.peak_id }) }"
    >
      <Column field="mz" header="m/z" sortable style="height: 20px; min-width: 6rem">
        <template #body="{ data }">
          {{ num.mz.format(data.mz) }}
        </template>
      </Column>
      <Column
        field="height"
        header="height"
        sortable
        :style="`height: 20px; min-width: ${peakAssignmentEnabled ? '5rem' : '6rem'}`"
      >
        <template #body="{ data }">
          {{ num.peakIntensity.format(data.height) }}
        </template>
      </Column>
      <Column
        v-if="!peakAssignmentEnabled"
        field="area"
        header="area"
        sortable
        style="height: 20px; min-width: 6rem"
      >
        <template #body="{ data }">
          {{ num.peakIntensity.format(data.area) }}
        </template>
      </Column>
      <!-- The matched isotope buttons are a peak's route into the Match tab,
           so this column follows that tab rather than the flag: it was hidden
           while the tab was retired, and comes back with it. -->
      <Column field="match" header="formula" sortable style="height: 20px">
        <template #body="{ data }">
          <div class="formula-buttons">
            <Button
              size="small"
              text
              severity="secondary"
              v-tooltip.top="
                `${match.target_compound_formula}${
                  app.data.ionization.mechanism.list.find(
                    (m) => m.ionization_mechanism_id === match.ionization_mechanism_id
                  )?.ionization_mechanism || ''
                }:\n ${match.target_ion_formula}`
              "
              @click="
                async () => {
                  if (data.match.length > 0) {
                    await app.data.match.visualized.set({
                      sampleId: app.data.sample.focusedId,
                      ionId: match.target_ion_id,
                      collectionId:
                        match.target_collection_ids.find(
                          (id) => id === app.data.match.collection.focusedId // In case the currently focused collection is among the matches, prioritize it
                        ) || match.target_collection_ids[0], // Otherwise just take the first one,
                      isotopeId: data.match[index].target_isotope_id
                    })
                    app.ui.tab.active = 'match'
                  }
                }
              "
              v-for="(match, index) in data.match"
              :key="match.target_isotope_id"
            >
              {{ match.target_isotope_formula }}
            </Button>
          </div>
        </template>
      </Column>
      <Column
        v-if="peakAssignmentEnabled"
        header="assignment"
        style="height: 20px; min-width: 9rem"
      >
        <template #body="{ data }">
          <div v-if="assignmentFor(data)" class="assignment-cell">
            <span class="formula" v-if="assignmentFor(data).assigned_formula">
              <span
                v-if="sourceIcon(assignmentFor(data).source)"
                :class="sourceIcon(assignmentFor(data).source)"
                class="source-icon"
                v-tooltip.top="assignmentFor(data).source"
              />
              {{ assignmentFor(data).assigned_formula }}
            </span>
            <BaseTierTag
              :tier="assignmentFor(data).tier"
              :evidence="assignmentFor(data).evidence"
              :role="assignmentFor(data).role"
              :source="assignmentFor(data).source"
            />
          </div>
          <span v-else-if="hasRun" class="empty">&mdash;</span>
        </template>
      </Column>
    </DataTable>
    <div v-else-if="app.data.peak.pending" class="center" style="width: 100%; height: 220px">
      <div class="col">
        <ProgressSpinner />
      </div>
    </div>
    <BaseLoadError
      v-else
      :error="app.data.peak.error"
      fallback="Could not load the peaks for this sample."
      :onRetry="() => app.data.peak.load('retry')"
      style="height: 220px"
    />
  </Panel>
</template>

<style scoped>
:deep(.p-panel-header) {
  display: flex !important;
}

:deep(.p-datatable .p-datatable-tbody > tr) {
  height: 36px !important;
}

.formula-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  align-items: flex-start;
  align-content: center;
}

.assignment-cell {
  display: flex;
  flex-flow: row wrap;
  gap: 0.3rem;
  align-items: center;
}

.formula {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.82rem;
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}

.source-icon {
  opacity: 0.5;
  font-size: 0.8rem;
}

.empty {
  opacity: 0.4;
}

.tier-summary {
  display: inline-flex;
  gap: 0.25rem;
  font-size: 0.72rem;
}

.tier-stat {
  min-width: 1.6rem;
  text-align: center;
  padding: 0.05rem 0.35rem;
  border-radius: 4px;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.tier-stat.assigned {
  color: var(--state-success);
  background: color-mix(in srgb, var(--state-success) 15%, transparent);
}
.tier-stat.candidate {
  color: var(--state-warning);
  background: color-mix(in srgb, var(--state-warning) 15%, transparent);
}
.tier-stat.below {
  color: var(--p-surface-500, #6f7889);
  background: color-mix(in srgb, var(--p-surface-500, #6f7889) 12%, transparent);
}
.tier-stat.unassigned {
  color: var(--p-surface-400, #9aa2b1);
  border: 1px dashed color-mix(in srgb, var(--p-surface-400, #9aa2b1) 50%, transparent);
}
</style>
