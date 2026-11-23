<script setup>
import { BaseVerdictBadge } from '@/lib/base'

/**
 * A row's verdict cell, in either scope: a button whichever state it is in,
 * so an unjudged row is judged from the same place a judged one is re-judged.
 * The badge is its whole content, and "none yet" draws nothing; the cell is
 * sized to hold a badge either way.
 *
 * The "none yet" state is `unjudged`, not `empty`: `.empty` is the browser
 * panes' empty-state panel class and carries a height. Stale: the judgment is
 * about a formula the batch consensus has since left, outlined in the warning
 * colour until re-judged or retracted.
 */
defineProps({
  /** The row's own verdict, or null. */
  record: { type: Object, default: null },
  /** A verdict reaching the row from the batch while it has none of its own. */
  overlay: { type: Object, default: null },
  /** A batch-level verdict that disagrees with the row's own, for its tooltip. */
  conflict: { type: Object, default: null },
  stale: { type: Boolean, default: false },
  /** History is read-only: the cell keeps its badge and loses its hand. */
  disabled: { type: Boolean, default: false },
  label: { type: String, required: true },
  tooltip: { type: String, default: '' }
})
defineEmits(['open'])
</script>

<template>
  <button
    type="button"
    class="verdict-cell"
    :class="{ stale, unjudged: !record && !overlay }"
    :disabled="disabled"
    :aria-label="label"
    v-tooltip.top="tooltip"
    @click.stop="$emit('open', $event)"
  >
    <BaseVerdictBadge v-if="record" :record="record" :conflict="conflict" compact />
    <BaseVerdictBadge v-else-if="overlay" :record="overlay" inherited compact />
  </button>
</template>

<style scoped>
.verdict-cell {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.6rem;
  min-height: 1.4rem;
  padding: 0.1rem 0.3rem;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.verdict-cell:hover {
  background: var(--p-content-hover-background);
}
.verdict-cell:disabled {
  cursor: default;
  opacity: 0.6;
}
.verdict-cell.stale {
  border-color: var(--state-warning);
}
</style>
