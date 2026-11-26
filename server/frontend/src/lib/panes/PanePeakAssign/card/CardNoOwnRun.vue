<script setup>
import { useId } from 'vue'

import Button from 'primevue/button'

import { useCard } from './useCard.js'

// The note under the answer on a sample the batch ledger serves, with no run
// of its own: what the card shows is the batch's reading for the sample, and
// Assign sample starts the run that fills the rest. In the provenance note's
// colour, not a warning's: it is a state, not an error
// (docs/dev/assignment_ledger_and_card.md, section 4.2).
const { noOwnRun, NO_OWN_RUN_TEXT, NO_OWN_RUN_TOOLTIP, assignRefusal, assignSample } = useCard()
const reasonId = useId()
</script>

<template>
  <div v-if="noOwnRun" class="no-own-run" role="status" data-testid="no-own-run">
    <div class="note-line" v-tooltip.top="NO_OWN_RUN_TOOLTIP">
      <span class="pi ph ph-stack note-icon" aria-hidden="true" />
      <span class="note-text" data-testid="no-own-run-text">{{ NO_OWN_RUN_TEXT }}</span>
    </div>
    <!-- The reason sits on a wrapper: a disabled button gets no hover. -->
    <span class="assign-wrap" v-tooltip.top="assignRefusal || null">
      <Button
        label="Assign sample"
        icon="pi ph ph-magic-wand"
        size="small"
        severity="secondary"
        data-testid="no-own-run-assign"
        :disabled="Boolean(assignRefusal)"
        :aria-describedby="assignRefusal ? reasonId : undefined"
        @click="assignSample"
      />
      <span v-if="assignRefusal" :id="reasonId" class="visually-hidden">{{ assignRefusal }}</span>
    </span>
  </div>
</template>

<style scoped>
.no-own-run {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.4rem 0.6rem;
  padding: 0.35rem 0.5rem;
  border: 1px solid color-mix(in srgb, var(--state-info) 45%, transparent);
  border-radius: 6px;
  background: color-mix(in srgb, var(--state-info) 10%, transparent);
  font-size: var(--card-text-sm);
}
.note-line {
  display: flex;
  flex: 1 1 12rem;
  align-items: baseline;
  gap: 0.4rem;
  min-width: 0;
  cursor: default;
}
.note-icon {
  color: var(--state-info);
}
.note-text {
  min-width: 0;
  overflow-wrap: anywhere;
}
.assign-wrap {
  display: inline-flex;
  pointer-events: auto;
}
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
