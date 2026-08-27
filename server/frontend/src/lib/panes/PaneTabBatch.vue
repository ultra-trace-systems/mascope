<script setup>
import { ref } from 'vue'
import SelectButton from 'primevue/selectbutton'

import { peakAssignmentEnabled } from '@/lib/features'
import { ChartBatchOverview, ChartBatchAssignments } from '@/lib/charts'
import { useApp } from '@/stores'

const app = useApp()

// The batch overview coexists in two modes: the legacy target-ion view and the
// peak-centric assignment view (batch peaks). See docs/dev/peak_assignment_batch.md.
// With peak-centric assignment off there is only the targeted view, so the
// toggle is hidden and the mode is pinned - the pre-feature layout, exactly as
// the ledger browser does it (PaneBrowserMatch).
const mode = ref('targets')
const modes = [
  { label: 'Targets', value: 'targets' },
  { label: 'Assignments', value: 'assignments' }
]
</script>

<template>
  <div class="batch-tab">
    <div
      v-if="peakAssignmentEnabled"
      class="mode-toggle"
      v-help.bottom_end="{
        message: `
          <h1>Batch Overview Mode</h1>
          <p>
          <b>Targets</b> plots the selected target collection's matched ions
          across the batch. <b>Assignments</b> plots the batch peaks selected in
          the ledger, with marker fill showing each species' consensus tier.
          </p>`,
        doc: app.ui.help.docUrl('how-it-works/peak-assignment/#batch-peaks')
      }"
    >
      <SelectButton
        v-model="mode"
        :options="modes"
        optionLabel="label"
        optionValue="value"
        :allowEmpty="false"
        aria-label="Batch overview mode"
      />
    </div>
    <ChartBatchOverview v-if="mode === 'targets' || !peakAssignmentEnabled" />
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
