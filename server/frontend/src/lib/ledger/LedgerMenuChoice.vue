<script setup>
/**
 * One single-select choice of a ledger's Show menu - the verdict filter,
 * Group by, a badge kind - as a labelled row of chips. Chips rather than a
 * Select: a Select would be a second overlay inside a focus-trapping one, and
 * PrimeVue's Select swallows Escape, so a keyboard user who tabbed into it
 * could neither close the menu nor tab out. Plain buttons keep Escape working,
 * and each is a tab stop that names itself.
 *
 * Deliberately the tier chip's shape: the strip and the menu filter the same
 * table, so a reader who has learned one has learned the other.
 */
defineProps({
  /** The row's visible label. */
  label: { type: String, required: true },
  /** The group's accessible name. */
  ariaLabel: { type: String, required: true },
  /** `{ value, label, tooltip? }` per chip. */
  options: { type: Array, required: true },
  /** Clicking the chip that is on turns it off, to `null`. */
  clearable: { type: Boolean, default: false }
})
const model = defineModel({ default: null })

function pick(option, clearable) {
  model.value = clearable && model.value === option.value ? null : option.value
}
</script>

<template>
  <div class="menu-choice" role="group" :aria-label="ariaLabel">
    <span class="menu-label">{{ label }}</span>
    <div class="menu-chips">
      <button
        v-for="option in options"
        :key="String(option.value)"
        type="button"
        class="menu-chip"
        :class="{ active: model === option.value }"
        :aria-pressed="model === option.value"
        :data-value="option.value"
        v-tooltip.top="option.tooltip"
        @click="pick(option, clearable)"
      >
        {{ option.label }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.menu-choice {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.menu-label {
  font-size: 0.8rem;
  opacity: 0.75;
}
.menu-chips {
  display: flex;
  flex-flow: row wrap;
  gap: 0.3rem;
}
.menu-chip {
  font-size: 0.72rem;
  padding: 0.15rem 0.5rem;
  border-radius: 100px;
  border: 1px solid var(--p-content-border-color, #e3e6ec);
  background: transparent;
  color: inherit;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
}
.menu-chip:hover,
.menu-chip.active {
  border-color: var(--p-primary-color, #6366f1);
}
.menu-chip.active {
  background: color-mix(in srgb, var(--p-primary-color, #6366f1) 12%, transparent);
}
</style>
