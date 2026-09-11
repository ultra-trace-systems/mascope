<script setup>
import { computed, inject } from 'vue'

import Chip from 'primevue/chip'

import { useApp } from '@/stores'
import { batchPeakLabel } from '@/lib/batchChart'
import { peakAssignmentEnabled } from '@/lib/features'
import { prettyTrim } from '@/lib/utils'

const app = useApp()

// How many selected species the tooltip names before it only counts the rest.
const TOOLTIP_NAMES = 10

// The Batch peaks ledger's selection is what the Assignments batch chart plots,
// and both are on screen only in Assignments mode (PaneTabBatch.vue), so that is
// where the chip reflects it. A selection kept while the browser shows targets
// is left alone - by the chip and by "clear all" - since nothing shows it there.
const selected = computed(() =>
  peakAssignmentEnabled && app.ui.matchMode.mode === 'assignments'
    ? app.data.batchPeak.selected
    : []
)
const active = computed(() => selected.value.length > 0)

const clear = () => {
  if (active.value) app.data.batchPeak.unfocus()
}

const register = inject('register-filter')
register({
  clear,
  active
})

const label = computed(() => {
  const names = selected.value.map(batchPeakLabel)
  if (names.length === 1) {
    return {
      short: prettyTrim(names[0], 30),
      full: `Batch peak selected:\n${names[0]}`
    }
  }
  const rest = names.length - TOOLTIP_NAMES
  const listed = names.slice(0, TOOLTIP_NAMES).join('\n')
  return {
    short: `${names.length} Batch peaks`,
    full: `Batch peaks selected:\n${listed}${rest > 0 ? `\n+${rest} more` : ''}`
  }
})
</script>

<template>
  <Chip
    v-if="active"
    icon="pi pi-chart-scatter"
    :label="label.short"
    v-tooltip.bottom="label.full"
    removable
    @remove="clear()"
  />
</template>
