<script setup>
import { computed } from 'vue'

import { TONES, markOf } from '@/lib/evidence/marks'

// One reading of a peak's assignment, drawn as the registry says
// (lib/evidence/marks.js): a 22 px box with the reading's glyph, in the colour
// of what it does to confidence, with its border in the same colour. The same
// component in the ledgers and in the inspector card, so a reading reads alike
// wherever it shows.
//
// A badge is a reading, not a state: the tier chip and the verdict are states
// (design system, section 8). The two share colours on purpose, so an amber
// badge beside an amber candidate chip says this reading is one of the
// reasons the row is a candidate.
//
// `quiet` is for a badge beside a line that already says the reading in words
// and carries its tooltip, as the card's batch lines do: the badge keeps its
// glyph and tone and drops its name and tooltip, which would say it twice.
const props = defineProps({
  mark: {
    type: String,
    required: true
  },
  record: {
    type: [Object, Boolean],
    default: null
  },
  quiet: {
    type: Boolean,
    default: false
  }
})

const entry = computed(() => markOf(props.mark))
const tone = computed(() => (entry.value && props.record ? entry.value.tone(props.record) : null))
// The colour is the registry's token, as the tier chips' are. The legend in
// docs/user/_help/assignment-tiers.md is written by hand: keep it in step.
const toneStyle = computed(() => (tone.value ? { '--tone': TONES[tone.value].color } : null))
const label = computed(() => (tone.value ? entry.value.summary(props.record) : ''))
// A quiet badge binds no tooltip at all (the template's first branch).
const tooltip = computed(() => (tone.value ? entry.value.tooltip(props.record) : ''))
</script>

<template>
  <span
    v-if="tone && quiet"
    :class="['evidence-badge', `tone-${tone}`]"
    :data-mark="mark"
    :data-tone="tone"
    :style="toneStyle"
    aria-hidden="true"
    ><span :class="['pi', 'ph', entry.glyph]"
  /></span>
  <span
    v-else-if="tone"
    :class="['evidence-badge', `tone-${tone}`]"
    :data-mark="mark"
    :data-tone="tone"
    :style="toneStyle"
    role="img"
    :aria-label="label"
    v-tooltip.top="tooltip"
    ><span :class="['pi', 'ph', entry.glyph]" aria-hidden="true"
  /></span>
</template>

<style scoped>
.evidence-badge {
  --tone: var(--p-text-muted-color);
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  width: 22px;
  height: 22px;
  border: 1px solid color-mix(in srgb, var(--tone) 55%, transparent);
  border-radius: 4px;
  background: color-mix(in srgb, var(--tone) 10%, transparent);
  color: var(--tone);
  font-size: 13px;
  line-height: 1;
  cursor: default;
}

/* A person's act: an outline in the text colour, which reads on both grounds,
   and never the accent, which means Mascope and the one primary action. */
.tone-human {
  border-color: var(--tone);
  background: transparent;
}
</style>
