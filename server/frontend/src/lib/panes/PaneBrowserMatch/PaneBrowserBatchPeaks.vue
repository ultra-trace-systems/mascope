<script setup>
import { ref, inject, computed } from 'vue'

import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { FilterMatchMode, FilterOperator } from '@primevue/core/api'

import { BaseTabbedPanel, BaseTierTag, BaseCopyableField } from '@/lib/base'
import { num } from '@/lib/formatters'
import { prettyTrim } from '@/lib/utils'
import { api } from '@/api'
import { useApp } from '@/stores'

/**
 * Batch-peak ledger: the selection surface for the peak-centric batch overview.
 * A virtual-scrolled, multi-select table of the batch's batch peaks (cross-sample
 * m/z anchors); the rows the user selects here are exactly what the Assignments
 * chart plots, so the chart never renders 1000+ traces at once.
 */
const app = useApp()
const table = ref(null)
const tableHeight = inject('match-table-height', ref(300))
const computing = ref(false)

const ledger = computed(() => app.data.batchPeak)

const tierOptions = ['identified', 'candidate', 'below_assignability', 'unassigned']

const filters = ref({
  consensus_formula: {
    operator: FilterOperator.AND,
    constraints: [{ value: null, matchMode: FilterMatchMode.CONTAINS }]
  },
  consensus_tier: {
    operator: FilterOperator.AND,
    constraints: [{ value: null, matchMode: FilterMatchMode.EQUALS }]
  }
})

const breadcrumb = computed(() => {
  const batch = app.data.batch.focused
  if (!batch) return null
  return {
    items: [
      {
        icon: 'pi pi-hashtag',
        label: prettyTrim(batch.sample_batch_name, 25),
        disabled: true,
        tooltip: `Batch peaks for ${batch.sample_batch_name}`
      },
      {
        icon: 'pi ph ph-atom',
        label: `${ledger.value.list.length} batch peaks`,
        disabled: true
      }
    ]
  }
})

// Ctrl+A selects all filtered rows (the virtual scroller only holds visible rows).
const onKeyDown = (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'a') {
    event.preventDefault()
    const rows = table.value?.processedData ?? ledger.value.list
    ledger.value.selected = [...rows]
  }
}

/** Backfill batch peaks from this batch's existing assignments; the ledger and
 *  chart refresh on the peak_assignment_reload event the task emits. */
async function computeBatchPeaks() {
  const batchId = app.data.batch.focusedId
  if (!batchId || computing.value) return
  computing.value = true
  try {
    await api.http.post(
      `/batch-peaks/batch/${batchId}/backfill`,
      {},
      { use: 'read', type: 'backfill_batch_peaks' }
    )
  } finally {
    computing.value = false
  }
}
</script>

<template>
  <BaseTabbedPanel :breadcrumb="breadcrumb" :loading="ledger.pending">
    <template #menu>
      <Button
        label="Compute batch peaks"
        icon="ph ph-arrows-clockwise"
        size="small"
        severity="secondary"
        :loading="computing"
        v-tooltip.left="'Build / refresh batch peaks from this batch\'s assignments'"
        @click="computeBatchPeaks"
      />
    </template>

    <DataTable
      ref="table"
      :value="ledger.list"
      dataKey="batch_peak_id"
      v-model:selection="ledger.selected"
      v-model:filters="filters"
      selectionMode="multiple"
      :metaKeySelection="false"
      @keydown="onKeyDown"
      filterDisplay="menu"
      resizableColumns
      size="small"
      scrollable
      :scrollHeight="`${tableHeight}px`"
      :virtualScrollerOptions="{ itemSize: 35.74 }"
      sortField="n_present"
      :sortOrder="-1"
    >
      <template #empty>
        No batch peaks yet - run "Compute batch peaks" (or assign the batch) to populate.
      </template>

      <Column selectionMode="multiple" style="width: 3rem" />

      <Column field="mz" header="m/z" sortable style="min-width: 7rem">
        <template #body="{ data }">{{ num.mz.format(data.mz) }}</template>
      </Column>

      <Column field="consensus_formula" header="Formula" sortable style="min-width: 9rem">
        <template #body="{ data }">
          <BaseCopyableField v-if="data.consensus_formula" :field="data.consensus_formula" />
          <span v-else class="unassigned">unassigned</span>
        </template>
        <template #filter="{ filterModel, filterCallback }">
          <InputText
            v-model="filterModel.value"
            @input="filterCallback()"
            placeholder="Search formula..."
            size="small"
          />
        </template>
      </Column>

      <Column
        field="consensus_tier"
        header="Tier"
        sortable
        style="min-width: 8rem"
        :filterMatchModeOptions="[{ label: 'Equals', value: 'equals' }]"
      >
        <template #body="{ data }">
          <BaseTierTag :tier="data.consensus_tier" :fit-score="data.best_fit_score" />
        </template>
        <template #filter="{ filterModel, filterCallback }">
          <Select
            v-model="filterModel.value"
            @change="filterCallback()"
            :options="tierOptions"
            placeholder="Any tier"
            size="small"
            :showClear="true"
          />
        </template>
      </Column>

      <Column
        field="n_present"
        header="Samples"
        sortable
        style="min-width: 6rem"
        v-tooltip="'Number of samples this species is seen in'"
      >
        <template #body="{ data }">{{ data.n_present }}</template>
      </Column>
    </DataTable>
  </BaseTabbedPanel>
</template>

<style scoped>
.unassigned {
  color: var(--p-text-muted-color, #888);
  font-style: italic;
}
</style>
