<script setup>
import { useCard } from './useCard.js'

// The notice under the answer when the card reads a run that is not the
// sample's result, named for what it is: an earlier run, a newer run that has
// not completed, the batch ledger, or a decoy with its arm (runNotice in
// cardReadings.js). The verdict bar and the changes by hand are off on each,
// and the notice's tooltip says why (docs/dev/assignment_ledger_and_card.md,
// section 4.2).
const { notice } = useCard()
</script>

<template>
  <div
    v-if="notice"
    class="run-notice"
    :class="notice.kind"
    role="status"
    :data-testid="`run-notice-${notice.kind}`"
    v-tooltip.top="notice.reason"
  >
    <!-- The lock the card's other read-only lines wear: what the notice says
         is that nothing here can be recorded. -->
    <span class="pi ph ph-lock-simple" aria-hidden="true" />
    <span class="notice-text">{{ notice.text }}</span>
  </div>
</template>

<style scoped>
.run-notice {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  padding: 0.3rem 0.5rem;
  border: 1px solid color-mix(in srgb, var(--state-info) 45%, transparent);
  border-radius: 6px;
  background: color-mix(in srgb, var(--state-info) 10%, transparent);
  font-size: var(--card-text-sm);
  cursor: default;
}
.run-notice.decoy {
  border-color: color-mix(in srgb, var(--state-warning) 45%, transparent);
  background: color-mix(in srgb, var(--state-warning) 10%, transparent);
}
.notice-text {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
