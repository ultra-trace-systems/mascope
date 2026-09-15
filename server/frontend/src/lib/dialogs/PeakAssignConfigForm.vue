<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import Button from 'primevue/button'
import FloatLabel from 'primevue/floatlabel'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import ToggleSwitch from 'primevue/toggleswitch'

import { useApp } from '@/stores'
import { PARAM_KEYS, isFormulaRange, usePeakAssignParams } from '@/lib/peakAssignParams'

// The peak-assignment run configuration, shared by the per-sample launcher and
// the batch launcher so both offer the same knobs and the same bounds.
//
// Defaults, bounds and the values themselves come from the shared parameter
// store rather than from a config object each caller owns: the limits are the
// constants PeakAssignmentConfig validates against, so an input can never offer
// a value the API would reject, and the values are the same ones the search
// pane binds - set here, they are what the pane searches with next, and the
// other way round. What the user sets persists; the reset control below puts it
// back. A caller reads `store.params` at launch time and applies whatever it
// decides for itself on top.

const props = defineProps({
  // Fields the caller has decided for the user and does not show: a launcher
  // that IS the untargeted stage pins `run_untargeted` on and hides its switch.
  // A hidden field is never bound, so it is never persisted from here and the
  // reset control below leaves it alone.
  hidden: {
    type: Array,
    default: () => []
  }
})

const app = useApp()
const store = usePeakAssignParams()
const params = store.params

onMounted(() => store.ensureLoaded())

// Help cards in this form live on the launcher dialogs' shared help layer:
// both hosts call app.ui.help.set('dialog_peak_assign') while their dialog is
// open, so the cards show only there and eclipse the dashboard's own cards.
const layer = 'dialog_peak_assign'
const vHelpLayer = app.ui.help.directive(layer)
const stagesDoc = app.ui.help.docUrl('how-it-works/peak-assignment/#the-two-stages')

// Reset clears exactly the fields this form shows. A hidden field was never the
// user's to set here, so it is not this control's to clear either.
const resettable = computed(() => PARAM_KEYS.filter((key) => !props.hidden.includes(key)))
const atDefaults = computed(() => store.isDefault(resettable.value))

// The range is committed to the store only once it parses, so a half-typed
// range is never persisted and never reaches the pane's next search. The raw
// text lives here meanwhile; the same two-model split the search pane uses.
const formulaRangeModel = ref(params.formula_ranges ?? '')
const formulaRangeValid = computed(
  () => !formulaRangeModel.value || isFormulaRange(formulaRangeModel.value)
)

watch(
  () => params.formula_ranges,
  (value) => {
    if (value != null && formulaRangeModel.value !== value) formulaRangeModel.value = value
  }
)

function commitFormulaRange() {
  if (formulaRangeValid.value && formulaRangeModel.value) {
    params.formula_ranges = formulaRangeModel.value.trim()
  }
}
</script>

<template>
  <div class="col config-form" style="gap: 1.25rem; align-items: stretch">
    <div
      v-if="!hidden.includes('run_untargeted')"
      class="toggle-row"
      v-help-layer.right="{
        message: `
          <h1>Untargeted Search</h1>
          <p>
          Assignment always matches peaks against the sample's known target
          library first. This switch adds the untargeted stage: a bounded
          composition search over the peaks the library leaves unexplained. It
          finds unknowns, and it is the slowest part of a run.
          </p>`,
        doc: stagesDoc
      }"
    >
      <ToggleSwitch v-model="params.run_untargeted" inputId="run_untargeted" />
      <label for="run_untargeted">
        Untargeted search
        <small>Search compositions for peaks the library leaves unassigned.</small>
      </label>
    </div>
    <FloatLabel
      :pt="
        app.ui.help.right(
          `
          <h1>m/z Precision</h1>
          <p>
          The mass tolerance of the untargeted stage, in ppm: candidate formulas
          must land within this window of the peak. Widening it finds more
          candidates, but slower and more ambiguous ones. The library stage is
          unaffected &mdash; it uses the sample's match parameters.
          </p>`,
          { layer, doc: stagesDoc }
        )
      "
    >
      <InputNumber
        v-model="params.mz_precision_ppm"
        inputId="mz_precision_ppm"
        :min="1"
        :max="store.limits.max_mz_precision_ppm"
        :disabled="!params.run_untargeted"
        fluid
      />
      <label for="mz_precision_ppm">m/z precision (ppm)</label>
    </FloatLabel>
    <FloatLabel
      :pt="
        app.ui.help.right(
          `
          <h1>Formula Range</h1>
          <p>
          Element-count bounds for untargeted candidate formulas, as
          space-separated ranges &mdash; e.g.
          <code>C0-80 H0-160 O0-50 N0-20</code>, isotopes in brackets
          (<code>[15N]0-1</code>). At most 12 element species; every added
          element multiplies the search space.
          </p>`,
          { layer, doc: stagesDoc }
        )
      "
    >
      <InputText
        v-model="formulaRangeModel"
        id="formula_ranges"
        :disabled="!params.run_untargeted"
        :invalid="!formulaRangeValid"
        @blur="commitFormulaRange"
        @keydown.enter="commitFormulaRange"
        v-tooltip.bottom="{
          value: 'Format: Element + range, e.g. C0-80 H0-160 [15N]0-1 ^N0-1',
          showDelay: 500
        }"
        fluid
      />
      <label for="formula_ranges">Formula range</label>
    </FloatLabel>
    <FloatLabel
      :pt="
        app.ui.help.right(
          `
          <h1>Max Untargeted Peaks</h1>
          <p>
          At most this many peaks enter the untargeted stage: the most intense
          of the peaks the library left unexplained, after the intensity
          threshold. Bounds the run time on dense spectra.
          </p>`,
          { layer, doc: stagesDoc }
        )
      "
    >
      <InputNumber
        v-model="params.max_untargeted_peaks"
        inputId="max_untargeted_peaks"
        :min="1"
        :max="store.limits.max_untargeted_peaks_ceiling"
        :disabled="!params.run_untargeted"
        fluid
      />
      <label for="max_untargeted_peaks">Max untargeted peaks</label>
    </FloatLabel>
    <FloatLabel
      :pt="
        app.ui.help.right(
          `
          <h1>Peak Intensity Threshold</h1>
          <p>
          Only unexplained peaks at least this intense (in the sample's native
          intensity units) enter the untargeted stage. Zero searches everything
          the peak cap allows.
          </p>`,
          { layer, doc: stagesDoc }
        )
      "
    >
      <InputNumber
        v-model="params.peak_intensity_threshold"
        inputId="peak_intensity_threshold"
        :min="0"
        :disabled="!params.run_untargeted"
        fluid
      />
      <label for="peak_intensity_threshold">Peak intensity threshold</label>
    </FloatLabel>
    <FloatLabel
      :pt="
        app.ui.help.right(
          `
          <h1>Max Alternatives Kept</h1>
          <p>
          How many runner-up candidates are stored per peak, from both stages.
          They appear as the close alternatives in the peak inspector.
          </p>`,
          {
            layer,
            doc: app.ui.help.docUrl(
              'how-it-works/peak-assignment/#arbitration-competing-the-candidates'
            )
          }
        )
      "
    >
      <InputNumber
        v-model="params.max_alternatives"
        inputId="max_alternatives"
        :min="0"
        :max="store.limits.max_alternatives_ceiling"
        fluid
      />
      <label for="max_alternatives">Max alternatives kept</label>
    </FloatLabel>
    <!-- These settings persist, so the way back to the shipped defaults has to
         be visible: without it a range narrowed once would quietly shape every
         later run. Disabled while nothing is overridden, which also makes the
         control double as the answer to "am I on the defaults?". -->
    <div class="reset-row">
      <Button
        label="Reset to defaults"
        icon="pi ph ph-arrow-counter-clockwise"
        size="small"
        text
        severity="secondary"
        :disabled="atDefaults"
        @click="store.reset(resettable)"
        v-help-layer.right="{
          message: `
            <h1>Reset to Defaults</h1>
            <p>
            These settings are remembered between runs and shared with the
            composition search. This puts every field above back to the value
            Mascope ships, and stops remembering yours.
            </p>`
        }"
      />
    </div>
  </div>
</template>

<style scoped>
.config-form :deep(small) {
  display: block;
  opacity: 0.7;
}

.toggle-row {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
}

/* Trails the fields it acts on rather than sharing the dialog footer: the
   footer is where the run is launched from, and a destructive-ish reset beside
   the primary action is a misclick waiting to happen. */
.reset-row {
  display: flex;
  justify-content: flex-end;
  margin-top: -0.5rem;
}
</style>
