<script setup>
import { TIERING_PROVISIONAL } from '@/lib/tiers'

// The small mark beside a tier heading - the ledgers' tier column headers and
// the inspector's tier row - saying the tiering is still being built. The text
// is the tier module's, so the three places cannot say it differently, and the
// mark disappears from all of them when that constant is set to null.
const mark = TIERING_PROVISIONAL
</script>

<template>
  <span
    v-if="mark"
    class="provisional-mark"
    data-testid="tiering-provisional"
    v-tooltip.top="mark.tooltip"
    >{{ mark.label }}</span
  >
</template>

<style scoped>
/* Small and in the warning colour, like the note it is: legible beside a
   column header without competing with the chips below it. */
.provisional-mark {
  margin-left: 0.35rem;
  padding: 0 0.3rem;
  border: 1px dashed var(--state-warning);
  border-radius: 3px;
  color: var(--state-warning);
  font-size: 0.62rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  text-transform: lowercase;
  vertical-align: middle;
  white-space: nowrap;
  cursor: help;
}
</style>
