<script setup>
// The muted line a part of the card shows where a run would fill it, on a row
// the batch ledger serves (MAS-1297): the Why box's other channels, the
// Isotope pattern's lines not found, the Identification level. One component,
// so the three keep one look.
defineProps({ text: { type: String, required: true } })
</script>

<template>
  <div class="ledger-note">
    <span class="pi ph ph-stack" aria-hidden="true" />
    {{ text }}
  </div>
</template>

<style scoped>
.ledger-note {
  display: flex;
  align-items: baseline;
  gap: 0.35rem;
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
}
.ledger-note .ph {
  color: var(--state-info);
}
</style>
