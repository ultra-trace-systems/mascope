<script setup>
import { ref, reactive, watch } from 'vue'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'

import { PeakAssignConfigForm } from '@/lib/dialogs'
import { useApp } from '@/stores'

import BatchPeakRunSelect from './BatchPeakRunSelect.vue'
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

// The search's parameters dialog: the untargeted stage's own settings (m/z
// precision, formula ranges, the peak ceiling, the intensity threshold, the
// alternatives kept), on the same form the per-sample launcher uses - with
// the stage itself pinned on and its switch hidden, since this button IS the
// untargeted stage. Reset on every open, as the per-sample launcher does, so
// a value typed for one search never carries silently into the next.
const searchVisible = ref(false)
function initialSearchConfig() {
  return {
    run_untargeted: true,
    mz_precision_ppm: null,
    formula_ranges: null,
    max_untargeted_peaks: null,
    peak_intensity_threshold: null,
    max_alternatives: null
  }
}
const searchConfig = reactive(initialSearchConfig())
watch(searchVisible, (open) => {
  if (open) Object.assign(searchConfig, initialSearchConfig())
  // The form's help cards live on the launcher dialogs' shared layer.
  app.ui.help.set(open ? 'dialog_peak_assign' : null)
})
async function launchSearch() {
  // Drop anything still unset so the backend default applies rather than a
  // null overriding it.
  const payload = Object.fromEntries(
    Object.entries(searchConfig).filter(([, value]) => value !== null && value !== '')
  )
  searchVisible.value = false
  await compute.searchUntargeted(payload)
}
</script>

<template>
  <!-- The tooltip hangs off the wrapper, not the button: a disabled button
       receives no mouse events, so a reason attached to it would be readable
       only in the one state where it says nothing. -->
  <span class="compute-bar" :class="{ blocked: compute.blockedReason !== null }">
    <!-- Which run the ledger is reading, beside the actions that make one. -->
    <BatchPeakRunSelect class="run-select-slot" />
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
        @click="searchVisible = true"
        :pt="
          app.ui.help.left(`
            <h1>Search Untargeted</h1>
            <p>
            Opens the search's parameters, then runs the untargeted composition
            search once per batch peak that
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
    <Dialog
      v-model:visible="searchVisible"
      modal
      header="Search untargeted"
      :style="{ width: '26rem' }"
    >
      <PeakAssignConfigForm
        :config="searchConfig"
        :pinned="['run_untargeted']"
        :hidden="['run_untargeted']"
      />
      <template #footer>
        <Button label="Cancel" text severity="secondary" @click="searchVisible = false" />
        <Button
          label="Search"
          icon="pi ph ph-magnifying-glass"
          :loading="compute.searching"
          @click="launchSearch"
        />
      </template>
    </Dialog>
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
/* The selector takes the left of the bar, the actions keep the right corner. */
.compute-bar > .run-select-slot {
  margin-right: auto;
}

/* Take the disabled button out of hit-testing so the wrapper above it receives
   the hover that shows why it is disabled. */
.compute-bar.blocked :deep(.p-button) {
  pointer-events: none;
}
</style>
