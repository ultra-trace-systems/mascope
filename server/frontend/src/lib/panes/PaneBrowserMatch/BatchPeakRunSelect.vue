<script setup>
import { computed } from 'vue'
import Select from 'primevue/select'

import { BaseRunProvenance } from '@/lib/base'
import { useApp } from '@/stores'

/**
 * Which batch run the Batch peaks ledger is showing.
 *
 * The batch counterpart of AssignmentRunBar's selector: the runs are the
 * batch-level operations that rewrote the ledger (a rebuild, an untargeted
 * search with its parameters, an import) and the folds that built it, newest
 * first, the current one being the live ledger. Picking an earlier run shows
 * the ledger as that run left it, read-only, from its snapshot.
 *
 * Rendered inside BatchPeakComputeBar, beside the actions that create runs, so
 * the thing the ledger is reading and the ways to change it sit together.
 */
const app = useApp()
const runs = computed(() => app.data.batchPeakRun)

const ACTION_LABELS = {
  fold: 'Folded samples',
  rebuild: 'Rebuild',
  search_untargeted: 'Untargeted search',
  import: 'Import'
}
const IN_FLIGHT_STATUSES = ['running']

function runLabel(run) {
  if (!run) return ''
  const list = runs.value.list
  const index = list.findIndex((r) => r.batch_peak_run_id === run.batch_peak_run_id)
  const ordinal = index === -1 ? '' : `#${list.length - index} · `
  const action = ACTION_LABELS[run.action] ?? run.action
  const state = run.current
    ? 'current'
    : `${run.status}${IN_FLIGHT_STATUSES.includes(run.status) ? '…' : ''}`
  return `${ordinal}${action} · ${state}`
}

const runOptions = computed(() => runs.value.list.map((run) => ({ ...run, _label: runLabel(run) })))
const selectedRun = computed({
  get: () => runs.value.focused,
  set: (run) => (run ? runs.value.focus(run) : runs.value.unfocus())
})
</script>

<template>
  <!-- Nothing at all until the batch has a ledger: a "Select run" box over an
       empty ledger would name a history that does not exist yet. -->
  <Select
    v-if="runOptions.length"
    v-model="selectedRun"
    :options="runOptions"
    optionLabel="_label"
    dataKey="batch_peak_run_id"
    size="small"
    placeholder="Select run"
    ariaLabel="Batch run"
    class="run-select"
    :pt="
      app.ui.help.bottom(
        { title: 'Batch Runs', helpKey: 'batch-runs' },
        { doc: app.ui.help.docUrl('how-it-works/peak-assignment/#batch-runs') }
      )
    "
  >
    <template #value="{ value, placeholder }">
      <span v-if="value" class="run-option">
        <span class="run-name">{{ runLabel(value) }}</span>
        <BaseRunProvenance :run="value" compact />
      </span>
      <span v-else>{{ placeholder }}</span>
    </template>
    <template #option="{ option }">
      <span class="run-option">
        <span class="run-name">{{ option._label }}</span>
        <BaseRunProvenance :run="option" />
      </span>
    </template>
  </Select>
</template>

<style scoped>
.run-select {
  min-width: 12rem;
  max-width: 22rem;
}
.run-option {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  min-width: 0;
}
.run-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
