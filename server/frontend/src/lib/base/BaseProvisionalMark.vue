<script setup>
import { TIERING_PROVISIONAL } from '@/lib/tiers'

// The small mark beside a tier heading - the ledgers' tier column headers and
// the inspector's tier row - saying the tiering is still being built. The text
// is the tier module's, so the three places cannot say it differently, and the
// mark disappears from all of them when that constant is set to null.
const mark = TIERING_PROVISIONAL

defineProps({
  // Also render the mark's short form, an asterisk, for a narrow ledger to
  // show in the word's place (lib/ledger/narrow.css). The word stays on the
  // page, the mark's accessible name, and the hover explains it. Off, the mark
  // is the word alone, as anywhere without those rules.
  collapsible: { type: Boolean, default: false }
})
</script>

<template>
  <span
    v-if="mark"
    class="provisional-mark"
    data-testid="tiering-provisional"
    v-tooltip.top="mark.tooltip"
    ><span class="provisional-word">{{ mark.label }}</span
    ><span v-if="collapsible" class="provisional-short" aria-hidden="true">*</span></span
  >
</template>

<style scoped>
/* Small and in the warning colour, like the note it is: legible beside a
   column header without competing with the chips below it. */
.provisional-mark {
  margin-left: 0.35rem;
  padding: 0 0.3rem;
  border: 1px solid var(--state-warning);
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
