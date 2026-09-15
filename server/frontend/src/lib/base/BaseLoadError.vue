<script setup>
import { computed } from 'vue'
import Button from 'primevue/button'

import { getApiErrorMessage } from '@/api/utils'

// Shown in place of a list whose load failed, so an empty pane is never
// unexplained. Muted rather than alarming - most causes are transient - and
// carries the retry itself, so every pane offers the same way back.
const props = defineProps({
  // The caught failure, or a ready-made message.
  error: {
    type: [Error, Object, String],
    required: false,
    default: null
  },
  // Message to show when the failure carries none of its own.
  fallback: {
    type: String,
    required: false,
    default: 'Could not load this list.'
  },
  // Optional retry action offered alongside the message.
  onRetry: {
    type: Function,
    required: false,
    default: null
  }
})

const message = computed(() =>
  typeof props.error === 'string' ? props.error : getApiErrorMessage(props.error, props.fallback)
)
</script>

<template>
  <div class="load-error">
    <div>
      <span class="pi ph ph-warning-circle" />
      <span class="message">{{ message }}</span>
      <Button
        v-if="onRetry"
        label="Try again"
        icon="pi ph ph-arrow-clockwise"
        @click="onRetry()"
        text
        size="small"
      />
    </div>
  </div>
</template>

<style scoped>
.load-error {
  display: grid;
  place-items: center;
  padding: 1rem;
  height: 100%;
  color: var(--p-text-muted-color);
}

.load-error div {
  display: grid;
  place-items: center;
  gap: 0.4rem;
  max-width: 30ch;
  text-align: center;
}

.ph-warning-circle {
  font-size: 1.4rem;
  opacity: 0.6;
}

.message {
  font-size: 0.9rem;
  line-height: 1.4;
}
</style>
