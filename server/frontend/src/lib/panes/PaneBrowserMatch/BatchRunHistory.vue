<script setup>
import { computed } from 'vue'

import { BaseRunProvenance } from '@/lib/base'
import { useApp } from '@/stores'
import {
  runClosedReason,
  runLabel,
  runOpenable,
  runStateWords
} from '@/stores/data/modules/batchPeak/run.js'

import { whenWords } from './stores/batchAssign.js'

/**
 * Run history: the batch's runs, newest first (docs/dev/batch_assignment_one_action.md,
 * 3.2). A pass is an *Assignment*, or *Assignment from a stage (...)*; a stage
 * run alone through its own route keeps its stage's name; imports and the
 * folds keep their labels.
 *
 * Picking an earlier run shows the ledger and the chart as that run left them,
 * read-only, from its snapshot; the bar then says so and offers Back to
 * current (BatchAssignControl). Opened in Assign batch's Details popover, from
 * its link or the menu's Run history.
 *
 * Only a run with a ledger to show opens: the current one, or one with a
 * snapshot. A failed, cancelled or running run stays listed, says why it
 * cannot be opened, and is no choice: its read would answer with no rows,
 * which reads as "that run left nothing".
 */
const emit = defineEmits(['picked'])

const app = useApp()
const runs = computed(() => app.data.batchPeakRun)

const rows = computed(() =>
  (runs.value?.list ?? []).map((run) => ({
    run,
    id: run.batch_peak_run_id,
    ordinal: runs.value.ordinal?.(run),
    label: runLabel(run),
    state: runStateWords(run),
    when: whenWords(run.batch_peak_run_utc_created),
    openable: runOpenable(run),
    reason: runClosedReason(run),
    viewing: runs.value.viewing?.batch_peak_run_id === run.batch_peak_run_id
  }))
)

function pick(run) {
  if (!runOpenable(run)) return
  if (run.current) runs.value.backToCurrent()
  else runs.value.focus(run)
  emit('picked', run)
}
</script>

<template>
  <div class="run-history" data-testid="run-history">
    <div
      class="history-heading"
      v-help.bottom="{
        title: 'Batch Runs',
        helpKey: 'batch-runs',
        doc: app.ui.help.docUrl('how-it-works/peak-assignment/#batch-runs')
      }"
    >
      Run history
    </div>
    <p v-if="!rows.length" class="history-empty">No runs yet.</p>
    <ul v-else class="history-list" role="listbox" aria-label="Batch runs">
      <li
        v-for="row in rows"
        :key="row.id"
        role="option"
        :aria-selected="row.viewing"
        class="history-row"
        :aria-disabled="row.openable ? undefined : 'true'"
        :class="{ viewing: row.viewing, current: row.run.current, closed: !row.openable }"
        :data-run="row.id"
        tabindex="0"
        @click="pick(row.run)"
        @keydown.enter.prevent="pick(row.run)"
      >
        <span class="row-ordinal">{{ row.ordinal != null ? `#${row.ordinal}` : '' }}</span>
        <span class="row-label" data-testid="run-label">{{ row.label }}</span>
        <span class="row-state" :class="`state-${row.run.current ? 'current' : row.run.status}`">
          {{ row.state }}
        </span>
        <span class="row-when">{{ row.when }}</span>
        <BaseRunProvenance :run="row.run" compact />
        <span v-if="row.reason" class="row-reason" data-testid="run-closed-reason">
          {{ row.reason }}
        </span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.run-history {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  min-width: 26rem;
  max-width: 32rem;
}
.history-heading {
  font-weight: 600;
}
.history-empty {
  margin: 0;
  color: var(--p-text-muted-color);
}
.history-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 22rem;
  overflow-y: auto;
}
.history-row {
  display: grid;
  grid-template-columns: 2.2rem minmax(0, 1fr) auto auto auto;
  align-items: center;
  gap: 0.5rem;
  padding: 0.3rem 0.4rem;
  border-radius: var(--p-border-radius-sm, 4px);
  cursor: pointer;
}
.history-row:not(.closed):hover,
.history-row:not(.closed):focus-visible {
  background: var(--p-content-hover-background);
}
/* A run with no ledger to show: listed, not a choice. */
.history-row.closed {
  cursor: default;
}
.history-row.closed:focus-visible {
  outline: 1px solid var(--p-focus-ring-color, var(--p-text-muted-color));
  outline-offset: -1px;
}
.row-reason {
  grid-column: 2 / -1;
  color: var(--p-text-muted-color);
  font-size: 0.85rem;
}
.history-row.viewing {
  background: var(--p-highlight-background);
  color: var(--p-highlight-color);
}
.row-ordinal,
.row-when {
  color: var(--p-text-muted-color);
  font-size: 0.85rem;
}
.row-label {
  /* Wraps rather than cut: a later-start pass's stage is what tells it apart. */
  overflow-wrap: anywhere;
}
.row-state {
  font-size: 0.85rem;
}
.state-current {
  font-weight: 600;
}
.state-failed,
.state-cancelled {
  color: var(--p-orange-500);
}
</style>
