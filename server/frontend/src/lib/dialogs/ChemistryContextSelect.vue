<script setup>
import { computed, onMounted } from 'vue'

import FloatLabel from 'primevue/floatlabel'
import Select from 'primevue/select'

import { usePeakAssignParams } from '@/lib/peakAssignParams'
import { contextName } from '@/lib/peakAssignProfiles'

// The chemistry context a dataset or a sample batch names for its samples
// (step 3.6 of the assignment quality plan): a peak assignment run whose
// context is Auto searches under it before the reagent profile's default, a
// batch's before its dataset's. The choices are the contexts the server
// offers a run config, so a name this selector writes is one the run reads.

const model = defineModel({ type: [String, null], default: null })

const props = defineProps({
  // The input's id, which the label names.
  id: {
    type: String,
    required: true
  },
  // Whose context this is: 'dataset' or 'batch', for the hint under it.
  owner: {
    type: String,
    default: 'dataset'
  },
  disabled: {
    type: Boolean,
    default: false
  }
})

const store = usePeakAssignParams()
onMounted(() => store.ensureLoaded())

const NOT_NAMED = {
  value: null,
  label: 'Not named',
  description: "A run on Auto takes the reagent profile's own context."
}

const options = computed(() => {
  const offered = store.presets.contexts.map((preset) => ({
    value: preset.name,
    label: contextName({ context: preset.name, context_label: preset.label }),
    description: preset.description
  }))
  // A name the server no longer offers is still shown as what is stored, so
  // the dialog does not read as if nothing were named.
  if (model.value && !offered.some((option) => option.value === model.value)) {
    offered.push({ value: model.value, label: model.value, description: null })
  }
  return [NOT_NAMED, ...offered]
})

const hint = computed(() =>
  props.owner === 'batch'
    ? "Peak assignment runs on Auto search the batch's samples under it, before the dataset's."
    : "Peak assignment runs on Auto search the dataset's samples under it, unless their batch names its own."
)
</script>

<template>
  <div class="chemistry-context-select">
    <FloatLabel>
      <Select
        v-model="model"
        :inputId="id"
        :options="options"
        optionLabel="label"
        optionValue="value"
        :disabled="disabled"
        fluid
      >
        <template #option="{ option }">
          <span v-tooltip.right="option.description || null">{{ option.label }}</span>
        </template>
      </Select>
      <label :for="id">Chemistry context</label>
    </FloatLabel>
    <small>{{ hint }}</small>
  </div>
</template>

<style scoped>
.chemistry-context-select {
  display: flex;
  flex-flow: column;
  gap: 0.25rem;
  margin-bottom: 1rem;
  text-align: left;
}
</style>
