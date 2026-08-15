<script setup>
import { ref } from 'vue'

import Button from 'primevue/button'
import Popover from 'primevue/popover'
import SelectButton from 'primevue/selectbutton'

import { BaseParamField } from '@/lib/base'

const chartSettings = ref()
const scale = defineModel('scale')
const yMode = defineModel('yMode')

</script>

<template>
  <Button
    v-tooltip.bottom="'Chart settings'"
    severity="secondary"
    text
    @click="
      (event) => {
        chartSettings.toggle(event)
      }
    "
    icon="pi pi-chart-bar"
  />
  <Popover ref="chartSettings">
    <div class="row" style="padding: 1rem; gap: 0.5rem">
      <BaseParamField
        label="Intensity scale"
        v-model:param="scale"
        :range="{ min: 0, max: 100000, step: 2000 }"
      />
    </div>
    <div class="row">
      <SelectButton v-model="yMode" :options="['average', 'sum']" :allowEmpty="false" />
    </div>
  </Popover>
</template>

<style scoped>
:deep(fieldset) {
  flex-flow: column nowrap;
  align-items: stretch;
  gap: 0.5rem;
}
</style>
