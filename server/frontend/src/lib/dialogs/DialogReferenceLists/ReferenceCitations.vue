<script setup>
import { computed, ref } from 'vue'

import Button from 'primevue/button'

import { CITATIONS_SHOWN, referenceUrl } from '@/lib/referenceSources'

// The publications a list cites, in its row of the Reference lists dialog:
// each citation with its DOI linked to doi.org, or its ISBN. A list citing
// many shows the first few and a button for the rest, so its row stays short.

const props = defineProps({
  references: { type: Array, default: () => [] }
})

const open = ref(false)
const shown = computed(() =>
  open.value ? props.references : props.references.slice(0, CITATIONS_SHOWN)
)
const hidden = computed(() => props.references.length - CITATIONS_SHOWN)
</script>

<template>
  <span v-if="!references.length">-</span>
  <ol v-else class="citations">
    <li v-for="(reference, i) in shown" :key="i" class="citation">
      <span>{{ reference.citation }}</span>
      <a
        v-if="referenceUrl(reference.doi)"
        :href="referenceUrl(reference.doi)"
        target="_blank"
        rel="noopener noreferrer"
        class="id"
        >{{ reference.doi }}</a
      >
      <span v-else-if="reference.isbn" class="id">ISBN {{ reference.isbn }}</span>
    </li>
    <li v-if="hidden > 0" class="more">
      <Button
        :label="open ? 'Show fewer' : `${hidden} more`"
        size="small"
        severity="secondary"
        text
        @click="open = !open"
      />
    </li>
  </ol>
</template>

<style scoped>
.citations {
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 0.85rem;
  min-width: 18rem;
}

.citation + .citation {
  margin-top: 0.35rem;
}

.id {
  display: block;
  white-space: nowrap;
}

.more {
  margin-top: 0.2rem;
}
</style>
