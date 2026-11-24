<script setup>
import { computed, ref, useId, watch } from 'vue'

import { fromLocalInput, toLocalInput } from '@/lib/verification'

/**
 * The time window a level-1 claim on a batch peak covers: the first and last
 * acquisition time its standard speaks for (docs/dev/identification_claims.md
 * section 5.4). Next week the same mass may be another isomer, so a member
 * acquired outside the window names no structure and reads the engine's
 * level; the verdict on the formula still reaches it.
 *
 * Shown in the reader's local time, to the second, both ends included: the
 * last second is taken whole, so a sample acquired part way through it is
 * inside. It starts from the window of the verdict being changed, else from
 * the batch's first and last acquisition (`range`), for the user to narrow.
 *
 * The model is `{ window_start_utc, window_end_utc, ready, problem }`, the
 * times as ISO strings in UTC.
 */
const props = defineProps({
  /** The verdict being changed: its `window_start_utc` and `window_end_utc`. */
  initial: { type: Object, default: null },
  /** The batch's first and last acquisition time, ISO, to start from. */
  range: { type: Object, default: null }
})
const model = defineModel({ type: Object, default: null })

const id = useId()

const first = ref(toLocalInput(props.initial?.window_start_utc ?? props.range?.first))
const last = ref(toLocalInput(props.initial?.window_end_utc ?? props.range?.last))

const value = computed(() => {
  const start = fromLocalInput(first.value)
  const end = fromLocalInput(last.value, { end: true })
  let problem = null
  if (!start || !end) problem = 'Give the first and last acquisition time the standard covers'
  else if (start > end) problem = 'The first acquisition time is after the last'
  return { window_start_utc: start, window_end_utc: end, ready: !problem, problem }
})
watch(value, (next) => (model.value = next), { immediate: true })
</script>

<template>
  <div class="claim-window" role="group" :aria-labelledby="`${id}-label`">
    <span :id="`${id}-label`" class="claim-label">Samples it covers, by acquisition time</span>
    <label class="claim-end"
      >First
      <input
        v-model="first"
        type="datetime-local"
        step="1"
        class="p-inputtext p-component p-inputtext-sm"
        aria-label="First acquisition time"
    /></label>
    <label class="claim-end"
      >Last
      <input
        v-model="last"
        type="datetime-local"
        step="1"
        class="p-inputtext p-component p-inputtext-sm"
        aria-label="Last acquisition time"
    /></label>
    <span class="claim-hint">
      {{
        value.problem ??
        'Outside it, a sample keeps the verdict on the formula, without the structure, and reads the engine’s level.'
      }}
    </span>
  </div>
</template>

<style scoped>
.claim-window {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.claim-label {
  font-size: 0.78rem;
  opacity: 0.8;
}
.claim-end {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  font-size: 0.85rem;
}
.claim-hint {
  font-size: 0.75rem;
  opacity: 0.7;
}
</style>
