<script setup>
import { onMounted, ref } from 'vue'

import FloatLabel from 'primevue/floatlabel'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import ToggleSwitch from 'primevue/toggleswitch'

import { api } from '@/api'

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
  }
})

const config = props.config

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
    <div class="toggle-row">
      <ToggleSwitch v-model="config.run_untargeted" inputId="run_untargeted" />
      <label for="run_untargeted">
        Untargeted search
        <small>Search compositions for peaks the library leaves unassigned.</small>
      </label>
    </div>
    <FloatLabel>
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
    <FloatLabel>
      <InputText
        v-model="config.formula_ranges"
        id="formula_ranges"
        :disabled="!config.run_untargeted"
        fluid
      />
      <label for="formula_ranges">Formula range</label>
    </FloatLabel>
    <FloatLabel>
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
    <FloatLabel>
      <InputNumber
        v-model="config.peak_intensity_threshold"
        inputId="peak_intensity_threshold"
        :min="0"
        :disabled="!config.run_untargeted"
        fluid
      />
      <label for="peak_intensity_threshold">Peak intensity threshold</label>
    </FloatLabel>
    <FloatLabel>
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
