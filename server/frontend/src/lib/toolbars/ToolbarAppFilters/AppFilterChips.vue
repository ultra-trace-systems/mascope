<script setup>
import { ref, watchEffect, provide } from 'vue'

import { useMagicKeys } from '@vueuse/core'

import Button from 'primevue/button'

import FilterSample from './FilterSample.vue'
import FilterTarget from './FilterTarget.vue'
import FilterMechanism from './FilterMechanism.vue'
import FilterBatchPeak from './FilterBatchPeak.vue'

const { alt, c } = useMagicKeys()

// provide a registration API
const filters = ref([])
provide('register-filter', (filter) => {
  filters.value.push(filter)
})

const clearAllFilters = () => {
  filters.value.forEach(({ clear }) => clear())
}
// clear all filters when alt+c is pressed
watchEffect(() => {
  if (alt.value && c.value) {
    clearAllFilters()
  }
})

const filtering = defineModel('filtering')
watchEffect(() => {
  filtering.value = filters.value.some(({ active }) => active)
})
</script>

<template>
  <menu>
    <Button
      v-if="filtering"
      icon="pi pi-filter-slash"
      @click="() => clearAllFilters()"
      label="clear all"
      v-tooltip.bottom="'alt+c'"
      text
      severity="secondary"
      style="opacity: 0.5"
    />
    <FilterSample />
    <FilterTarget />
    <FilterMechanism />
    <FilterBatchPeak />
    <span v-if="filtering" class="pi pi-filter" style="opacity: 0.5" />
  </menu>
</template>

<style scoped>
menu {
  display: flex;
  flex-flow: row;
  align-items: center;
  gap: 0.5rem;
  padding: 0;
}
</style>
