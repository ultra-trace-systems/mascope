<script setup>
import { BaseEvidenceBadge } from '@/lib/base'
import { markOf } from '@/lib/evidence/marks'

// Across the batch, opened from a row's "+n": every badge of the row, the
// ones the cell shows included, each as the cell draws it and followed by
// its reading in words, the text the badge's own tooltip gives. A badge that
// does something in the row - opens the pairs behind a grade, or the rows a
// reading names - is a button here too, and says so by emitting `act` with
// the reading; the pane does what the row's badge does.
//
// The badge is quiet beside its line (design system, section 8): the line
// says the reading, so the badge drops its name and tooltip, and a button's
// accessible name is the action the row's badge names.
defineProps({
  // [{ id, record, action }], in the cell's order: `action` is null, or
  // { label } for a badge that acts.
  readings: {
    type: Array,
    required: true
  }
})
const emit = defineEmits(['act'])

const readingText = (reading) => markOf(reading.id).tooltip(reading.record)
</script>

<template>
  <div class="across-popover" data-testid="across-popover">
    <span class="title">Across the batch</span>
    <ul class="readings">
      <li
        v-for="reading in readings"
        :key="reading.id"
        class="reading"
        :data-testid="`across-reading-${reading.id}`"
      >
        <button
          v-if="reading.action"
          type="button"
          class="badge-button"
          :data-testid="`across-act-${reading.id}`"
          :aria-label="reading.action.label"
          @click.stop="emit('act', reading)"
        >
          <BaseEvidenceBadge :mark="reading.id" :record="reading.record" quiet />
        </button>
        <BaseEvidenceBadge v-else :mark="reading.id" :record="reading.record" quiet />
        <span class="text" data-testid="across-reading-text">{{ readingText(reading) }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.across-popover {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  width: 26rem;
  max-width: 90vw;
}
.title {
  font-weight: 600;
}
.readings {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.reading {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
}
.text {
  padding-top: 0.15rem;
  font-size: 0.8rem;
  line-height: 1.35;
  white-space: pre-line;
}
.badge-button {
  display: inline-flex;
  flex: none;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: none;
  cursor: pointer;
}
.badge-button :deep(.evidence-badge) {
  cursor: pointer;
}
</style>
