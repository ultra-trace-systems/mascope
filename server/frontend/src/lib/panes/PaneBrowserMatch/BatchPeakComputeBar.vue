<script setup>
import Button from 'primevue/button'

import { useApp } from '@/stores'

import { useBatchPeakCompute } from './stores/batchPeakCompute.js'

/**
 * The way to build or refresh the batch ledger, at batch level.
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
  <span class="compute-bar" :class="{ blocked: compute.blockedReason !== null }">
    <span v-tooltip.left="compute.computeTooltip">
      <Button
        label="Rebuild batch ledger"
        icon="pi ph ph-arrows-clockwise"
        size="small"
        :loading="compute.computing"
        :disabled="compute.blockedReason !== null"
        @click="compute.compute()"
        :pt="
          app.ui.help.left(`
          <h1>Rebuild Batch Ledger</h1>
          <p>
          Folds every sample of the batch into the batch peaks: a sample with
          an assignment run folds from it, one without is assigned from the
          known compositions and folded without a run, the way a new sample
          is. New samples fold in automatically; use this to populate a batch
          that predates the ledger or was never assigned, or to refresh after
          an import.
          </p>
        `)
        "
      />
    </span>
    <!-- The untargeted search, once per species: the batch counterpart of the
         untargeted stage a per-sample run offers. Same gate as the compute, and
         held while a compute is in flight. -->
    <span v-tooltip.left="compute.searchTooltip">
      <Button
        label="Search untargeted"
        icon="pi ph ph-magnifying-glass"
        size="small"
        severity="secondary"
        :loading="compute.searching"
        :disabled="compute.blockedReason !== null || compute.computing"
        @click="compute.searchUntargeted()"
        :pt="
          app.ui.help.left(`
            <h1>Search Untargeted</h1>
            <p>
            Runs the untargeted composition search once per batch peak that
            nothing has assigned yet &mdash; on its brightest peak, in that
            sample's own spectrum &mdash; and then measures the composition it
            found against every other sample the species was seen in. It
            writes no per-sample runs; the results appear in this ledger and
            in each sample's view.
            </p>
          `)
        "
      />
    </span>
  </span>
</template>

<style scoped>
/* Takes the width the switch beside it does not, so the button lands in the
   bar's right corner - where "Assign peaks" lands once a sample is focused. */
.compute-bar {
  display: inline-flex;
  justify-content: flex-end;
  gap: 0.35rem;
  flex: 1 1 auto;
  min-width: 0;
}

/* Take the disabled button out of hit-testing so the wrapper above it receives
   the hover that shows why it is disabled. */
.compute-bar.blocked :deep(.p-button) {
  pointer-events: none;
}
</style>
