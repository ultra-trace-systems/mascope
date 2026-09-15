<script setup>
import { peakAssignmentEnabled } from '@/lib/features'
import { ChartBatchOverview, ChartBatchAssignments } from '@/lib/charts'
import { useApp } from '@/stores'

const app = useApp()

// The batch overview coexists in two modes: the legacy target-ion view and the
// peak-centric assignment view (batch peaks). See docs/dev/peak_assignment_batch.md.
// This tab carries no switch of its own: the mode is the shared
// `app.ui.matchMode` the browser shell's switch bar sets (PaneBrowserMatch), so
// the chart always plots the paradigm the browser is showing - the assignments
// chart plots exactly what the batch-peaks ledger has selected, and that ledger
// is only on screen in Assignments mode. The store pins targets with the flag
// off; the guard below keeps that true even if something assigns the mode
// anyway, which is the pre-feature layout.
</script>

<template>
  <div class="batch-tab">
    <ChartBatchOverview v-if="app.ui.matchMode.mode === 'targets' || !peakAssignmentEnabled" />
    <ChartBatchAssignments v-else />
  </div>
</template>
