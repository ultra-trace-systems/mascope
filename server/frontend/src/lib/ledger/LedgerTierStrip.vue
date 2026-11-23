<script setup>
import { ROLE_BUCKETS } from '@/lib/tiers'
import { useApp } from '@/stores'

/**
 * The ledger's tier strip: a chip per tier and per role with its count, each a
 * filter. Multi-select in both scopes: a chip toggles its bucket, and the
 * table shows the buckets that are on, or every row while none is.
 *
 * A chip is a filter control, so its number is the number of rows clicking it
 * produces: the scope counts its top-level rows, an isotopologue line with its
 * compound, over the whole ledger rather than the filtered rows - a histogram
 * that reacted to its own filter would collapse to one bucket the moment it was
 * used. The roles after the tiers count the peaks the source and the
 * instrument made, which are neither. The scope's own items (the verification
 * queue, the view menu) follow in the default slot.
 */
defineProps({
  /** `ledgerTierChips` of the scope's counts. */
  chips: { type: Array, required: true },
  /**
   * Whether the chips show their counts: not while the ledger's rows failed to
   * load, when the counts would be the last good load's.
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
    <!-- Says whose tiers the chips count while that is not this server's. -->
    <span v-if="engineView" class="engine-view-label" v-tooltip.top="engineTooltip"
      >engine's tiers</span
    >
    <button
      v-for="chip in chips"
      :key="chip.key"
      type="button"
      class="tier-stat"
      :class="[
        chip.key,
        {
          'roles-start': chip.key === ROLE_BUCKETS[0],
          active: active.has(chip.key),
          dim: active.size && !active.has(chip.key)
        }
      ]"
      :aria-pressed="active.has(chip.key)"
      v-tooltip.top="active.has(chip.key) ? `Showing ${chip.label}` : `Filter to ${chip.label}`"
      @click="$emit('toggle', chip.key)"
    >
      <b v-if="counted">{{ chip.count }}</b> {{ chip.label }}
    </button>
    <slot />
  </div>
</template>

<style scoped>
.tier-strip {
  display: flex;
  flex-flow: row wrap;
  align-items: center;
  gap: 0.3rem;
  padding: 0 0.4rem;
  flex: 0 0 auto;
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
.tier-stat.roles-start {
  margin-left: 0.4rem;
}
.engine-view-label {
  font-size: 0.72rem;
  font-style: italic;
  opacity: 0.7;
}
</style>
