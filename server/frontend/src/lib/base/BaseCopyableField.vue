<script setup>
import { ref } from 'vue'

import Button from 'primevue/button'

import { copyText } from '@/lib/clipboard'

const { field, tooltip } = defineProps({
  field: {
    required: true
  },
  tooltip: {
    type: String,
    default: null
  }
})

const emit = defineEmits(['copy'])

// Used inside dialogs and drawers, whose focus trap would pull focus back out of
// a temporary text field on <body>: the fallback copy puts its field in here.
const root = ref()

async function copyField(text) {
  const copied = await copyText(String(text), root.value)
  if (!copied) console.warn('Could not copy the field to the clipboard')
  return copied
}
</script>

<template>
  <span ref="root" class="field">
    <span v-tooltip.top="tooltip">{{ field }}</span>
    <Button
      v-if="field && String(field).length > 0"
      v-tooltip.bottom="{ value: 'Copy to clipboard', showDelay: 1000 }"
      icon="pi pi-clone"
      severity="secondary"
      text
      size="small"
      @click="
        async (event) => {
          event.stopPropagation()
          // Only signal 'copy' once the clipboard write actually succeeded: a
          // listener may discard the value on copy (a one-time password), and a
          // failed write in a non-secure context must not throw it away unread.
          if (await copyField(field)) emit('copy')
        }
      "
    />
    <slot></slot>
  </span>
</template>

<style scoped>
.field {
  display: flex;
  flex-flow: row;
  align-items: center;
}

.field > :deep(button) {
  visibility: hidden;
}

.field:hover > :deep(button) {
  visibility: visible;
}

:deep(button) {
  width: min-content;
  margin-left: 0.5rem;
  padding: 5px 7px;
}
</style>
