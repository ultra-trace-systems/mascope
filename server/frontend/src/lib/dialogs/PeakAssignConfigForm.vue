<script setup>
import { onMounted, ref } from 'vue'

import FloatLabel from 'primevue/floatlabel'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import ToggleSwitch from 'primevue/toggleswitch'

import { api } from '@/api'
import { useApp } from '@/stores'

// The peak-assignment run configuration, shared by the per-sample launcher and
// the batch launcher so both offer the same knobs and the same bounds.
//
// Defaults and limits come from /params rather than being duplicated here: the
// limits are the constants PeakAssignmentConfig validates against, so an input
// can never offer a value the API would reject. The caller owns the config
// object (and therefore the defaults that matter to it, notably whether the
// untargeted stage starts on), so this only fills in what it has not set.

const props = defineProps({
  // The caller's reactive config object. Mutated in place rather than bound with
  // v-model: it is already reactive, and the form only ever sets its fields, so
  // a two-way binding on the object itself would add nothing but a compiler
  // warning about reassigning a const.
  config: {
    type: Object,
    required: true
  },
  // Fields the caller has set deliberately and this form must not overwrite
  // when the server defaults arrive.
  pinned: {
    type: Array,
    default: () => []
  },
  // Fields the caller has decided for the user and does not show: a launcher
  // that IS the untargeted stage pins `run_untargeted` on and hides its switch.
  hidden: {
    type: Array,
    default: () => []
  }
})

const config = props.config

const app = useApp()

// Help cards in this form live on the launcher dialogs' shared help layer:
// both hosts call app.ui.help.set('dialog_peak_assign') while their dialog is
// open, so the cards show only there and eclipse the dashboard's own cards.
const layer = 'dialog_peak_assign'
const vHelpLayer = app.ui.help.directive(layer)
const stagesDoc = app.ui.help.docUrl('how-it-works/peak-assignment/#the-two-stages')

// Generous fallbacks used only until /params answers; replaced on mount.
const limits = ref({
  max_untargeted_peaks_ceiling: 5000,
  max_mz_precision_ppm: 100,
  max_alternatives_ceiling: 50
})

onMounted(() => {
  api.http
    .get('/params', { type: 'read_params' })
    .then(({ data }) => {
      const params = data?.data?.params
      if (params?.peak_assignment_limits) limits.value = params.peak_assignment_limits
      const defaults = params?.peak_assignment
      if (!defaults) return
      for (const [key, value] of Object.entries(defaults)) {
        // Only fill fields this form actually exposes, never a pinned one, and
        // never clobber something the user has already touched.
        if (!(key in config)) continue
        if (props.pinned.includes(key)) continue
        if (config[key] === null || config[key] === undefined) {
          config[key] = value
        }
      }
    })
    .catch(() => {
      // Bounds and defaults are a convenience; the API validates regardless.
    })
})
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
      <ToggleSwitch v-model="config.run_untargeted" inputId="run_untargeted" />
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
        v-model="config.mz_precision_ppm"
        inputId="mz_precision_ppm"
        :min="1"
        :max="limits.max_mz_precision_ppm"
        :disabled="!config.run_untargeted"
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
          <code>C0-100 H0-100 O0-100 N0-100</code>, isotopes in brackets
          (<code>[15N]0-1</code>). At most 12 element species; every added
          element multiplies the search space.
          </p>`,
          { layer, doc: stagesDoc }
        )
      "
    >
      <InputText
        v-model="config.formula_ranges"
        id="formula_ranges"
        :disabled="!config.run_untargeted"
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
        v-model="config.max_untargeted_peaks"
        inputId="max_untargeted_peaks"
        :min="1"
        :max="limits.max_untargeted_peaks_ceiling"
        :disabled="!config.run_untargeted"
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
        v-model="config.peak_intensity_threshold"
        inputId="peak_intensity_threshold"
        :min="0"
        :disabled="!config.run_untargeted"
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
        v-model="config.max_alternatives"
        inputId="max_alternatives"
        :min="0"
        :max="limits.max_alternatives_ceiling"
        fluid
      />
      <label for="max_alternatives">Max alternatives kept</label>
    </FloatLabel>
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
</style>
