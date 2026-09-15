<script setup>
import SelectButton from 'primevue/selectbutton'

// Trace draw style; plotly mode strings, selectable in a chart's settings menu.
const drawModes = [
  { label: 'Markers', value: 'markers' },
  { label: 'Lines', value: 'lines' },
  { label: 'Both', value: 'lines+markers' }
]

const mode = defineModel()
</script>

<template>
  <div class="row">
    <span>Draw style</span>
    <SelectButton
      v-model="mode"
      :options="drawModes"
      optionLabel="label"
      optionValue="value"
      :allowEmpty="false"
    />
  </div>
</template>
