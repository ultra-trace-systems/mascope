<script setup>
import { computed } from 'vue'

import { useApp } from '@/stores'

/**
 * The ledger's tier bar: a 7 px proportion bar over the tier chips, each chip
 * a filter with its count (docs/dev/frontend_design_system.md, section 9).
 * Multi-select in both scopes: a chip toggles its bucket, and the table shows
 * the buckets that are on, or every row while none is.
 *
 * A chip is a filter control, so its number is the number of rows clicking it
 * produces: the scope counts its top-level rows, an isotopologue line with its
 * compound, over the whole ledger rather than the filtered rows - a histogram
 * that reacted to its own filter would collapse to one bucket the moment it was
 * used. A chip with nothing in it is not drawn (`ledgerTierChips`). The bar
 * draws the tiers' shares in their order and colours; the roles (reagent,
 * artifact), which count the peaks the source and the instrument made and are
 * no confidence, are left out of it and sit apart after the tiers. The scope's
 * own items (the verification queue) follow at the right, in the default slot.
 */
const props = defineProps({
  /** `ledgerTierChips` of the scope's counts. */
  chips: { type: Array, required: true },
  /**
   * Whether the chips show their counts, and the bar its shares: not while the
   * ledger's rows failed to load, when the counts would be the last good
   * load's.
   */
  counted: { type: Boolean, default: true },
  /** The buckets whose chips are on. */
  active: { type: Set, required: true },
  /** Whether the chips count the producing engine's tiers, and what that means. */
  engineView: { type: Boolean, default: false },
  engineTooltip: { type: String, default: '' }
})
defineEmits(['toggle'])

const app = useApp()

const tierChips = computed(() => props.chips.filter((chip) => !chip.role))
const roleChips = computed(() => props.chips.filter((chip) => chip.role))

// The bar's segments: each tier's share of the tiered rows, in the tiers'
// order. Empty when nothing is tiered, so no bar of zero width is drawn.
const segments = computed(() => {
  const total = tierChips.value.reduce((sum, chip) => sum + chip.count, 0)
  if (!total) return []
  return tierChips.value
    .filter((chip) => chip.count > 0)
    .map((chip) => ({ key: chip.key, share: chip.count / total, label: chip.label }))
})
const barLabel = computed(() =>
  segments.value
    .map((segment) => `${Math.round(segment.share * 100)} % ${segment.label}`)
    .join(', ')
)
</script>

<template>
  <div
    class="tier-strip"
    v-help.top="{
      title: 'Confidence Tiers',
      helpKey: 'assignment-tiers',
      doc: app.ui.help.docUrl('how-it-works/peak-assignment/#confidence-tiers')
    }"
  >
    <div
      v-if="counted && segments.length"
      class="tier-bar"
      role="img"
      :aria-label="`Tiers: ${barLabel}`"
      data-testid="tier-bar"
    >
      <span
        v-for="segment in segments"
        :key="segment.key"
        class="tier-segment"
        :class="segment.key"
        :style="{ flexGrow: segment.share }"
        :data-share="segment.share.toFixed(4)"
      />
    </div>
    <div class="tier-chips">
      <!-- Says whose tiers the chips count while that is not this server's. -->
      <span v-if="engineView" class="engine-view-label" v-tooltip.top="engineTooltip"
        >engine's tiers</span
      >
      <button
        v-for="chip in tierChips"
        :key="chip.key"
        type="button"
        class="tier-stat"
        :class="[
          chip.key,
          { active: active.has(chip.key), dim: active.size && !active.has(chip.key) }
        ]"
        :aria-pressed="active.has(chip.key)"
        v-tooltip.top="active.has(chip.key) ? `Showing ${chip.label}` : `Filter to ${chip.label}`"
        @click="$emit('toggle', chip.key)"
      >
        <b v-if="counted">{{ chip.count }}</b> {{ chip.label }}
      </button>
      <!-- The source's and the instrument's peaks, apart: what follows is not
           a confidence. -->
      <span v-if="roleChips.length" class="role-chips" data-testid="role-chips">
        <button
          v-for="chip in roleChips"
          :key="chip.key"
          type="button"
          class="tier-stat"
          :class="[
            chip.key,
            { active: active.has(chip.key), dim: active.size && !active.has(chip.key) }
          ]"
          :aria-pressed="active.has(chip.key)"
          v-tooltip.top="active.has(chip.key) ? `Showing ${chip.label}` : `Filter to ${chip.label}`"
          @click="$emit('toggle', chip.key)"
        >
          <b v-if="counted">{{ chip.count }}</b> {{ chip.label }}
        </button>
      </span>
      <span v-if="$slots.default" class="strip-end"><slot /></span>
    </div>
  </div>
</template>

<style scoped>
.tier-strip {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  padding: 0 0.4rem;
  flex: 0 0 auto;
}
.tier-chips {
  display: flex;
  flex-flow: row wrap;
  align-items: center;
  gap: 0.3rem;
}
/* The tiers' shares, in their order and their state colours. */
.tier-bar {
  display: flex;
  height: 7px;
  border-radius: 4px;
  overflow: hidden;
  background: var(--p-content-hover-background);
}
.tier-segment {
  flex-basis: 0;
  min-width: 2px;
}
.tier-segment.assigned {
  background: var(--state-success);
}
.tier-segment.candidate {
  background: var(--state-warning);
}
.tier-segment.below_assignability {
  background: var(--p-surface-400, #9aa2b1);
}
.tier-segment.unassigned,
.tier-segment.engine_unstated {
  background: var(--p-surface-300, #c3c8d2);
}
.role-chips {
  display: inline-flex;
  flex-flow: row wrap;
  gap: 0.3rem;
  margin-left: 0.6rem;
}
.strip-end {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  margin-left: auto;
}
:slotted(.tier-stat),
.tier-stat {
  font-size: 0.72rem;
  padding: 0.15rem 0.5rem;
  border-radius: 100px;
  border: 1px solid var(--p-content-border-color, #e3e6ec);
  font-variant-numeric: tabular-nums;
  /* button reset */
  background: transparent;
  color: inherit;
  font-family: inherit;
  cursor: pointer;
  transition:
    opacity 0.12s,
    border-color 0.12s,
    background 0.12s;
}
:slotted(.tier-stat:hover),
.tier-stat:hover {
  border-color: var(--p-primary-color, #6366f1);
}
:slotted(.tier-stat.active),
.tier-stat.active {
  border-color: var(--p-primary-color, #6366f1);
  background: color-mix(in srgb, var(--p-primary-color, #6366f1) 12%, transparent);
}
.tier-stat.dim {
  opacity: 0.4;
}
:slotted(.tier-stat b),
.tier-stat b {
  font-weight: 700;
}
.tier-stat.assigned b {
  color: var(--state-success);
}
.tier-stat.candidate b {
  color: var(--state-warning);
}
.tier-stat.below_assignability b,
.tier-stat.unassigned b,
.tier-stat.engine_unstated b {
  color: var(--p-surface-500, #6f7889);
}
/* The source's and the instrument's peaks, in the colour their chips wear, and
   set off from the tiers: what follows is not a confidence. */
.tier-stat.reagent b,
.tier-stat.artifact b {
  color: var(--role-reagent);
}
.engine-view-label {
  font-size: 0.72rem;
  font-style: italic;
  opacity: 0.7;
}
</style>
