<script setup>
import { ref } from 'vue'
import SelectButton from 'primevue/selectbutton'

import { ChartBatchOverview, ChartBatchAssignments } from '@/lib/charts'

// The batch overview coexists in two modes: the legacy target-ion view and the
// peak-centric assignment view (batch peaks). See docs/dev/peak_assignment_batch.md.
const mode = ref('targets')
const modes = [
  { label: 'Targets', value: 'targets' },
  { label: 'Assignments', value: 'assignments' }
]
</script>

<template>
  <div class="batch-tab">
    <div class="mode-toggle">
      <SelectButton
        v-model="mode"
        :options="modes"
        optionLabel="label"
        optionValue="value"
        :allowEmpty="false"
        aria-label="Batch overview mode"
      />
    </div>
    <ChartBatchOverview v-if="mode === 'targets'" />
    <ChartBatchAssignments v-else />
  </div>
</template>

<style scoped>
.mode-toggle {
  display: flex;
  justify-content: flex-end;
  padding: 0.25rem 0.5rem 0;
}
</style>
