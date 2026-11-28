<script setup>
import { useId } from 'vue'

import { useSectionOpen } from './useCard.js'

// One folded section of the card: a heading line with its summary, which
// unfolds the section's body (docs/dev/assignment_ledger_and_card.md, section
// 4.1). Folded by default, so the card's first screen is the answer, the
// reasons and the verdict. The body is kept in the page while folded
// (`v-show`), so what it holds is in the document for the screen reader that
// unfolds it and for the tooltips it binds. Its open state is the card's, by
// the section's test id (`useSectionOpen`), so it holds while the card moves
// between the lines of one isotope pattern.
const props = defineProps({
  title: { type: String, required: true },
  summary: { type: String, default: '' },
  // The summary in full, on hover, where the folded line is too narrow for it.
  summaryTooltip: { type: String, default: null },
  testid: { type: String, default: null }
})

const open = useSectionOpen(props.testid)
const bodyId = useId()
</script>

<template>
  <section class="card-section" :class="{ open }" :data-testid="testid">
    <button
      type="button"
      class="section-head"
      :aria-expanded="open ? 'true' : 'false'"
      :aria-controls="bodyId"
      data-testid="section-toggle"
      @click="open = !open"
    >
      <span :class="['pi', 'ph', open ? 'ph-caret-down' : 'ph-caret-right', 'section-caret']" />
      <span class="section-title">{{ title }}</span>
      <span class="section-summary" v-tooltip.top="summaryTooltip"
        ><slot name="summary">{{ summary }}</slot></span
      >
    </button>
    <div v-show="open" :id="bodyId" class="section-body">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.card-section {
  border-top: 1px solid var(--p-content-border-color);
}
.section-head {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  width: 100%;
  padding: 0.4rem 0;
  border: none;
  background: none;
  color: inherit;
  font: inherit;
  font-size: var(--card-text-base);
  text-align: left;
  cursor: pointer;
}
.section-head:hover .section-title,
.section-head:focus-visible .section-title {
  text-decoration: underline;
}
.section-caret {
  flex: none;
  color: var(--p-text-muted-color);
}
.section-title {
  flex: none;
  font-weight: 500;
}
/* The folded line's summary: one line, cut where the column ends. */
.section-summary {
  min-width: 0;
  overflow: hidden;
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.section-body {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0 0 0.6rem;
}
</style>
