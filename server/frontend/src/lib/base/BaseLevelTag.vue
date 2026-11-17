<script setup>
import { computed } from 'vue'

import { levelMeta, levelTooltip } from '@/lib/levels'

// Identification-level chip: what a reader may state of a row, in the scale
// peaky and this server share (lib/levels.js). It sits beside the tier chip and
// never replaces it - the tier says how sure the run is of its reading, the
// level what the reading may claim.
//
// The chip writes the level itself ("4a") inside its colour, so the colour is
// never the only thing that says it. The colour follows the number's band
// alone, in the level's own tokens (style.css), so 4a and 4b wear the same
// one: the letters name a kind of evidence, not a rank.
//
// A row with no level renders nothing. Such a row was never judged - a
// reagent's ion, an imported row, a run from before the level - and a grey
// chip would read as 5b. The ledgers draw their own dash in the cell, as they
// do for every empty one.
const props = defineProps({
  level: {
    type: String,
    default: null
  },
  // The line naming what earned the level, and what would raise it, as the
  // row records them; the hover text adds them under the level's meaning.
  evidence: {
    type: String,
    default: null
  },
  lift: {
    type: String,
    default: null
  },
  // An isotopologue holds its M0's level, and the hover text says so.
  role: {
    type: String,
    default: null
  },
  tooltip: {
    type: String,
    default: null
  }
})

const meta = computed(() => levelMeta(props.level))

const autoTooltip = computed(() =>
  props.tooltip != null
    ? props.tooltip
    : levelTooltip({
        level: props.level,
        evidence: props.evidence,
        lift: props.lift,
        role: props.role
      })
)
</script>

<template>
  <span v-if="meta" class="level-tag" v-tooltip.top="autoTooltip">
    <span
      :class="['level', `level-${meta.number}`, `level-band-${meta.band}`]"
      :style="{ '--level-color': `var(${meta.token})` }"
      >{{ meta.label }}</span
    >
  </span>
</template>

<style scoped>
.level-tag {
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
}

/* The level's text is short ("4a"); a fixed minimum width keeps a column of
   them aligned, so a reader scans the colours down the ledger. The band's
   token draws the text and the border, and a tint of it the ground, so the
   text keeps the token's contrast on either theme. */
.level {
  display: inline-flex;
  justify-content: center;
  min-width: 2.2rem;
  padding: 0.1rem 0.4rem;
  border: 1px solid var(--level-color);
  border-radius: 6px;
  color: var(--level-color);
  background: color-mix(in srgb, var(--level-color) 12%, transparent);
  font-size: 11px;
  font-weight: 600;
  line-height: 1.4;
}
</style>
