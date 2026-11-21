<script setup>
import Button from 'primevue/button'

import { useCard } from './useCard.js'

// A change by hand, said in one line with its action - "Assigned by hand in
// place of C3H4O4 · Undo" - where the card used to say it in fifty words. The
// whole sentence is the line's tooltip (docs/dev/assignment_ledger_and_card.md,
// section 4.1).
const {
  focusedAssignment,
  manualDemoted,
  manualLine,
  manualSentence,
  undoEntry,
  undoOverride,
  curating,
  curateDenied,
  batchOverride,
  batchPinLine,
  batchPinSentence,
  releasing,
  releaseOverride
} = useCard()
</script>

<template>
  <!-- A row the server stripped: never anyone's choice, so the eraser rather
       than the hand, for the reason the tier chip uses one (BaseTierTag.vue). -->
  <div
    v-if="focusedAssignment.source === 'manual' && manualDemoted"
    class="manual-note"
    data-testid="manual-note"
    v-tooltip.top="manualSentence"
  >
    <span class="pi ph ph-eraser demoted-icon" />
    <span class="note-text">{{ manualLine }}</span>
  </div>
  <div
    v-else-if="focusedAssignment.source === 'manual'"
    class="manual-note"
    data-testid="manual-note"
    v-tooltip.top="manualSentence"
  >
    <span class="pi ph ph-hand-pointing manual-icon" />
    <span class="note-text">{{ manualLine }}</span>
    <template v-if="undoEntry && !curateDenied">
      <span class="sep">&middot;</span>
      <Button
        label="Undo"
        size="small"
        text
        class="note-action"
        data-testid="manual-undo"
        :disabled="curating !== null"
        :loading="curating === 0"
        v-tooltip.top="'Put back the assignment this replaced'"
        @click="undoOverride"
      />
    </template>
  </div>
  <div
    v-if="batchOverride"
    class="manual-note"
    data-testid="batch-pin-note"
    v-tooltip.top="batchPinSentence"
  >
    <span class="pi ph ph-hand-pointing manual-icon" />
    <span class="note-text">{{ batchPinLine }}</span>
    <span class="sep">&middot;</span>
    <Button
      label="Release"
      size="small"
      text
      class="note-action release-link"
      data-testid="batch-release"
      :disabled="releasing || curateDenied"
      :loading="releasing"
      v-tooltip.top="
        'Let the batch decide again: the samples measured for this identity go back to what they read before'
      "
      @click="releaseOverride"
    />
  </div>
</template>

<style scoped>
.manual-note {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  min-width: 0;
  font-size: var(--card-text-sm);
  cursor: default;
}
.note-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sep {
  color: var(--p-text-muted-color);
}
.note-action {
  flex: none;
  padding: 0 0.25rem;
  font-size: inherit;
}
/* "A person chose this" reads at full strength, as the same mark does on the
   tier chip: in the human tone, the text colour, never the accent. */
.manual-note > .manual-icon {
  color: var(--p-text-color);
}
/* The eraser does not: a demoted row is the consequence of a decision taken on
   another peak, not a decision about this one. */
.manual-note > .demoted-icon {
  color: var(--p-text-muted-color);
}
</style>
