<script setup>
import Button from 'primevue/button'

import { useApp } from '@/stores'

import { useBatchPeakCompute } from './stores/batchPeakCompute.js'

/**
 * The way to build or refresh the batch peaks, at batch level.
 *
 * The batch counterpart of AssignmentRunBar: it takes the same corner of
 * PaneBrowserMatch's switch bar that "Assign peaks" takes once a sample is
 * focused, so the action that fills the ledger sits in one place whichever of
 * the two ledgers is showing. It used to live in the ledger's own panel header,
 * where it shared a row with the isotopologue switch - which now hangs off the
 * filter row's view menu, again matching the sample level.
 *
 * Rendered inside the switch bar, so it inherits the bar's feature-flag gate
 * rather than carrying a second copy of it; the launch itself is in the store,
 * because the ledger beside it renders the refusal.
 */
const app = useApp()
const compute = useBatchPeakCompute()
</script>

<template>
  <!-- The tooltip hangs off the wrapper, not the button: a disabled button
       receives no mouse events, so a reason attached to it would be readable
       only in the one state where it says nothing. -->
  <span
    class="compute-bar"
    :class="{ blocked: compute.blockedReason !== null }"
    v-tooltip.left="compute.computeTooltip"
  >
    <Button
      label="Compute batch peaks"
      icon="pi ph ph-arrows-clockwise"
      size="small"
      :loading="compute.computing"
      :disabled="compute.blockedReason !== null"
      @click="compute.compute()"
      :pt="
        app.ui.help.left(`
          <h1>Compute Batch Peaks</h1>
          <p>
          Builds or refreshes the batch peaks from the assignment runs this
          batch's samples already have &mdash; no new assignment work. New
          runs fold in automatically; use this to populate a batch assigned
          before batch peaks existed, or to refresh after an import.
          </p>
        `)
      "
    />
  </span>
</template>

<style scoped>
/* Takes the width the switch beside it does not, so the button lands in the
   bar's right corner - where "Assign peaks" lands once a sample is focused. */
.compute-bar {
  display: inline-flex;
  justify-content: flex-end;
  flex: 1 1 auto;
  min-width: 0;
}

/* Take the disabled button out of hit-testing so the wrapper above it receives
   the hover that shows why it is disabled. */
.compute-bar.blocked :deep(.p-button) {
  pointer-events: none;
}
</style>
