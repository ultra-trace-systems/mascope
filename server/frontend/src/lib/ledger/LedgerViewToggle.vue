<script setup>
import ToggleSwitch from 'primevue/toggleswitch'

/**
 * One labelled switch of a ledger's view menu. Label first, control last, by
 * reversing the row rather than the markup, so the switch stays first in the
 * panel and Tab reaches it before anything after it. The switch's only
 * accessible name is its `<label for>`, which is why the menu is a Popover of
 * plain markup rather than a Menu.
 */
defineProps({
  id: { type: String, required: true },
  label: { type: String, required: true },
  tooltip: { type: String, default: '' },
  // On the menu's first switch: Popover moves focus only to a genuinely
  // focusable `[autofocus]` child, and ToggleSwitch puts a fallthrough
  // attribute on its root div, so it goes on the input. Without it the panel
  // opens with nothing focused and, teleported to the end of <body>, is
  // unreachable by keyboard.
  autofocus: { type: Boolean, default: false }
})
const model = defineModel({ type: Boolean, default: false })
</script>

<template>
  <div class="unfold-toggle" v-tooltip.top="tooltip">
    <ToggleSwitch
      v-model="model"
      :inputId="id"
      :pt="autofocus ? { input: { autofocus: true } } : undefined"
    />
    <label :for="id">{{ label }}</label>
  </div>
</template>

<style scoped>
.unfold-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
  font-size: 0.8rem;
  white-space: nowrap;
  flex-direction: row-reverse;
}
.unfold-toggle label {
  cursor: pointer;
  opacity: 0.75;
}
</style>
