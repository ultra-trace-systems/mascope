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
  // Whose level it is where a reader could be shown either, the live claim's
  // or the engine's (readerLevel in lib/levels.js), and under a claim the
  // engine's own: the hover text says which. Left out where only one can be.
  source: {
    type: String,
    default: null
  },
  engineLevel: {
    type: String,
    default: null
  },
  // The level comes from the batch - a batch-level verdict's, or the batch
  // peak's own on a row the batch ledger serves (readerLevel): the batch's own
  // mark, the `#` an inherited verdict wears, small at the chip's upper right.
  fromBatch: {
    type: Boolean,
    default: false
  },
  tooltip: {
    type: String,
    default: null
  },
  // An importing engine's own level beside the members', drawn outlined with
  // no ground, as the engine's tier chip is.
  outlined: {
    type: Boolean,
    default: false
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
        role: props.role,
        source: props.source,
        engineLevel: props.engineLevel,
        fromBatch: props.fromBatch
      })
)
</script>

<template>
  <span
    v-if="meta"
    class="level-tag"
    :class="{ outlined, 'from-batch': fromBatch }"
    v-tooltip.top="autoTooltip"
  >
    <span
      :class="['level', `level-${meta.number}`, `level-band-${meta.band}`]"
      :style="{ '--level-color': `var(${meta.token})` }"
      >{{ meta.label }}</span><span
      v-if="fromBatch"
      class="pi pi-hashtag batch-mark"
      data-testid="level-batch-mark"
      aria-hidden="true"
    />
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
/* From the batch: its mark at the chip's upper right, at 9 px, as an inherited
   verdict wears it (BaseVerdictBadge); the one difference from a level the
   sample's own run or verdict states. */
.level-tag.from-batch {
  position: relative;
  margin-right: 5px;
}
.batch-mark {
  position: absolute;
  top: -4px;
  right: -6px;
  font-size: 9px;
  line-height: 1;
}
/* An engine's own level, beside the members': the border and the word, with
   no ground, as the engine's tier chip is. */
.level-tag.outlined .level {
  background: transparent;
}
</style>
