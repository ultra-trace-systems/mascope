<script setup>
import { ref, watch } from 'vue'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'

import { PeakAssignConfigForm } from '@/lib/dialogs'
import { usePeakAssignParams } from '@/lib/peakAssignParams'
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
// alternatives kept), on the same form the per-sample launcher uses - with the
// stage's own switch hidden, since this button IS the untargeted stage.
//
// The values come from the shared parameter store, so what the user set in the
// per-sample launcher or the composition search pane is what this dialog opens
// on, and what they set here persists in turn. `run_untargeted` is the one
// field that does not: it is hidden here, so the form never binds it and this
// dialog can neither read nor write the user's setting for it - the launch
// forces it on instead.
const searchVisible = ref(false)
const searchParams = usePeakAssignParams()
watch(searchVisible, (open) => {
  // The form's help cards live on the launcher dialogs' shared layer.
  app.ui.help.set(open ? 'dialog_peak_assign' : null)
})
async function launchSearch() {
  const payload = searchParams.payload({ run_untargeted: true })
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
        :disabled="
          compute.blockedReason !== null ||
          compute.computing ||
          compute.detecting ||
          compute.reading ||
          compute.stamping ||
          compute.stripping ||
          compute.checking ||
          compute.deciding ||
          compute.measuring
        "
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
    <!-- The batch's homologous series, walked from what the batch already
         assigns: no parameters of its own, so it launches straight away.
         Held while the others rewrite or read the ledger. -->
    <span v-tooltip.left="compute.detectTooltip">
      <Button
        label="Detect series"
        icon="pi ph ph-stairs"
        size="small"
        severity="secondary"
        :loading="compute.detecting"
        :disabled="
          compute.blockedReason !== null ||
          compute.computing ||
          compute.searching ||
          compute.reading ||
          compute.stamping ||
          compute.stripping ||
          compute.checking ||
          compute.deciding ||
          compute.measuring
        "
        @click="compute.detectSeries()"
        :pt="
          app.ui.help.left(`
            <h1>Detect Series</h1>
            <p>
            Walks the batch's homologous series &mdash; one CH2, O, H2O, CO,
            CO2 or C2H2O further, and the siloxane and CF2 ladders &mdash; from
            every batch peak the batch assigns, and proposes the formula a step
            makes wherever a batch peak nothing assigned, or one held only as a
            candidate, sits at its exact mass. Each kind of step is walked again
            at shifted masses that land on no chemistry, and only the kinds
            whose proposals beat those decoys are kept. A kept proposal is
            measured in every sample its batch peak is in, and the row says
            which series it belongs to. It writes no per-sample runs.
            </p>
          `)
        "
      />
    </span>
    <!-- The co-variation of the batch's channels: read off the ledger as it
         stands, with no parameters, and display-only. Held while the others
         rewrite the ledger. -->
    <span v-tooltip.left="compute.readTooltip">
      <Button
        label="Read co-variation"
        icon="pi ph ph-wave-sine"
        size="small"
        severity="secondary"
        :loading="compute.reading"
        :disabled="
          compute.blockedReason !== null ||
          compute.computing ||
          compute.searching ||
          compute.detecting ||
          compute.stamping ||
          compute.stripping ||
          compute.checking ||
          compute.deciding ||
          compute.measuring
        "
        @click="compute.readCoVariation()"
        :pt="
          app.ui.help.left(`
            <h1>Read Co-variation</h1>
            <p>
            For every compound the batch sees through two ionization channels
            or more, reads how the channels' intensities follow each other
            across the batch: in log intensity, divided by each spectrum's
            reagent signal, with what all the batch's peaks share taken out.
            Two channels whose correlation is 0.6 or more co-vary. A pair held
            in fewer than ten spectra, or with a channel that hardly moves,
            cannot be read. The reading shows in the Co-variation column and
            in the peak inspector. It moves no tier.
            </p>
          `)
        "
      />
    </span>
    <!-- The batch's predicted diagnostic isotopologues: read off the ledger as
         it stands, with no parameters, and recorded only. Held while the
         others rewrite or read the ledger. -->
    <span v-tooltip.left="compute.stampTooltip">
      <Button
        label="Stamp isotopologues"
        icon="pi ph ph-stamp"
        size="small"
        severity="secondary"
        :loading="compute.stamping"
        :disabled="
          compute.blockedReason !== null ||
          compute.computing ||
          compute.searching ||
          compute.detecting ||
          compute.reading ||
          compute.stripping ||
          compute.checking ||
          compute.deciding ||
          compute.measuring
        "
        @click="compute.stampIsotopologues()"
        :pt="
          app.ui.help.left(`
            <h1>Stamp Isotopologues</h1>
            <p>
            For every batch peak the batch commits a formula to, predicts its
            15N, 18O, 34S, 29Si, 30Si, 37Cl and 81Br lines, looks for the batch
            peak at each one's mass, and in every sample that holds both reads
            the line's height against the share the formula predicts, inside
            the tolerance that sample's run fitted. A line whose height holds in
            at least eight of every ten samples that can tell, over three or
            more, is stamped as the peak's isotopologue in each of them, and
            shows under its main peak with its isotope. It moves no tier.
            </p>
          `)
        "
      />
    </span>
    <!-- The batch's merged isotopologue rows: read off the ledger as it
         stands, with no parameters. It rewrites the rows it strips, in the
         batch and in their samples, so it is held while the others rewrite or
         read the ledger, and they while it runs. -->
    <span v-tooltip.left="compute.stripTooltip">
      <Button
        label="Strip isotopologue rows"
        icon="pi ph ph-scissors"
        size="small"
        severity="secondary"
        :loading="compute.stripping"
        :disabled="
          compute.blockedReason !== null ||
          compute.computing ||
          compute.searching ||
          compute.detecting ||
          compute.reading ||
          compute.stamping ||
          compute.checking ||
          compute.deciding ||
          compute.measuring
        "
        @click="compute.stripIsotopologues()"
        :pt="
          app.ui.help.left(`
            <h1>Strip Isotopologue Rows</h1>
            <p>
            Reads every batch peak the batch commits a formula to against the
            13C, 15N, 18O, 34S, 29Si, 30Si, 37Cl and 81Br lines of every
            brighter one below it. Where a peak sits at such a line and, over
            three or more samples, its height over the other's is a median 0.4
            to 1.4 times the share the line predicts, with fewer than one
            sample in ten above twice it, the peak is that compound's
            isotopologue rather than a compound of its own: it folds under the
            compound, and in each sample its row becomes the compound's isotope
            line. Peaks curated by hand, assigned by hand in a sample, or with
            a 13C line of their own are never stripped.
            </p>
          `)
        "
      />
    </span>
    <!-- The batch checks: read off the ledger as it stands, with no
         parameters, and recorded only. Held while the others rewrite or read
         the ledger, and they while it runs. -->
    <span v-tooltip.left="compute.checkTooltip">
      <Button
        label="Read batch checks"
        icon="pi ph ph-seal-check"
        size="small"
        severity="secondary"
        :loading="compute.checking"
        :disabled="
          compute.blockedReason !== null ||
          compute.computing ||
          compute.searching ||
          compute.detecting ||
          compute.reading ||
          compute.stamping ||
          compute.stripping ||
          compute.deciding ||
          compute.measuring
        "
        @click="compute.readBatchChecks()"
        :pt="
          app.ui.help.left(`
            <h1>Read Batch Checks</h1>
            <p>
            Reads three facts only the batch can state. A <b>halogen lock</b>:
            a compound with chlorine or bromine whose 37Cl or 81Br line
            <i>Stamp isotopologues</i> stamped, sitting at the exact spacing
            from it; where a silicon line could sit there instead, the 29Si line
            decides. A <b>14N twin</b>: a labelled ion's impurity line one unit
            down, present or absent where the label's purity says it must show.
            A <b>uronium pair</b>: a compound's protonated and urea-clustered
            peaks moving together, as <i>Read co-variation</i> reads them. They
            show in the Facts column. No tier moves on them.
            </p>
          `)
        "
      />
    </span>
    <!-- The batch's known species: decided off the ledger as it stands, with
         no parameters. It rewrites the samples it claims a species in, in the
         batch and in their samples, so it is held while the others rewrite or
         read the ledger, and they while it runs. -->
    <span v-tooltip.left="compute.decideTooltip">
      <Button
        label="Decide known species"
        icon="pi ph ph-list-checks"
        size="small"
        severity="secondary"
        :loading="compute.deciding"
        :disabled="
          compute.blockedReason !== null ||
          compute.computing ||
          compute.searching ||
          compute.detecting ||
          compute.reading ||
          compute.stamping ||
          compute.stripping ||
          compute.checking ||
          compute.measuring
        "
        @click="compute.decideKnownSpecies()"
        :pt="
          app.ui.help.left(`
            <h1>Decide Known Species</h1>
            <p>
            Decides each known species once for the batch: every ion of the
            source's own library a sample's reagent pass claimed, and every
            compound a reference list tags as background. A species whose peak
            is in half the samples or more, and that one sample read as the
            species on its own evidence, is present: it is claimed in every
            sample its peak is in, its rows rewritten. Samples assigned by hand,
            named by the target library, or reading the peak as another ion's
            line or claim are kept, and listed with their readings.
            </p>
          `)
        "
      />
    </span>
    <!-- The reagent ions' water ladders: measured off the ledger as it
         stands, with no parameters. It rewrites the samples it claims a rung
         in, in the batch and in their samples, so it is held while the others
         rewrite or read the ledger, and they while it runs. -->
    <span v-tooltip.left="compute.measureTooltip">
      <Button
        label="Measure water ladders"
        icon="pi ph ph-drop"
        size="small"
        severity="secondary"
        :loading="compute.measuring"
        :disabled="
          compute.blockedReason !== null ||
          compute.computing ||
          compute.searching ||
          compute.detecting ||
          compute.reading ||
          compute.stamping ||
          compute.stripping ||
          compute.checking ||
          compute.deciding
        "
        @click="compute.measureWaterLadders()"
        :pt="
          app.ui.help.left(`
            <h1>Measure Water Ladders</h1>
            <p>
            Reads how far each reagent ion's water ladder reaches on the
            batch's own samples: Br-.(H2O)n, NO3-.(H2O)n and the like, up to
            45 waters, in each stretch of acquisition the batch splits into at
            its long gaps. A hydrate that, with every hydrate below it, is in
            half the stretch's samples, three times as often as at the masses
            beside it, and on a TOF shows no carbon, is the reagent's ion: its
            peak is claimed as such in that stretch's samples, its rows
            rewritten. Samples assigned by hand, named by a target or reference
            list, or reading the peak as another ion's line or claim are kept,
            and listed with their readings.
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
        :hidden="['run_untargeted']"
        :sample-batch-id="app.data.batch.focusedId"
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
