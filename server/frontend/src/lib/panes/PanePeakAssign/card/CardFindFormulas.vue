<script setup>
import Button from 'primevue/button'

import { useCard } from './useCard.js'

// The composition search for the focused peak, under the verdict bar and on
// the card of a peak with no ledger row: it searches the peak's own mass, so
// it needs no assignment to start from.
const { showSearch, toggleSearch } = useCard()
</script>

<template>
  <div class="insp-actions">
    <Button
      :label="showSearch ? 'Hide the formula search' : 'Find other formulas for this peak'"
      size="small"
      text
      :severity="showSearch ? 'primary' : 'secondary'"
      icon="pi ph ph-magnifying-glass"
      data-testid="find-other-formulas"
      v-tooltip.top="'Search other compositions for this peak'"
      @click="toggleSearch"
    />
  </div>
</template>

<style scoped>
.insp-actions {
  display: flex;
  justify-content: flex-start;
}
</style>
