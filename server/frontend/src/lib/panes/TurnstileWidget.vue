<script setup>
// Cloudflare's Turnstile widget, for the sign-in form.
//
// Emits `token` with the solved token, and with null whenever there is no
// usable one: before it is solved, once it expires, on an error. A token is
// good for one sign-in, so the form calls `reset()` after every attempt,
// whatever its outcome.

import Message from 'primevue/message'

import { onBeforeUnmount, onMounted, ref } from 'vue'

import { loadTurnstile, TURNSTILE_ACTION } from '@/lib/turnstile'

const props = defineProps({
  siteKey: { type: String, required: true }
})

const emit = defineEmits(['token'])

const container = ref(null)
const failed = ref(false)

let api = null
let widgetId = null
let unmounted = false

onMounted(async () => {
  try {
    api = await loadTurnstile()
  } catch {
    failed.value = true
    return
  }
  if (unmounted || !container.value) return
  widgetId = api.render(container.value, {
    sitekey: props.siteKey,
    action: TURNSTILE_ACTION,
    theme: 'auto',
    callback: (token) => emit('token', token),
    'expired-callback': () => emit('token', null),
    'error-callback': () => emit('token', null)
  })
})

onBeforeUnmount(() => {
  unmounted = true
  if (api && widgetId !== null) api.remove(widgetId)
})

/** Discard the current token and ask for a fresh one. */
const reset = () => {
  emit('token', null)
  if (api && widgetId !== null) api.reset(widgetId)
}

defineExpose({ reset })
</script>

<template>
  <div class="turnstile">
    <div ref="container" data-testid="turnstile"></div>
    <Message v-if="failed" severity="error">
      The check that you are human could not be loaded, so you cannot sign in. Reload the page to
      try again.
    </Message>
  </div>
</template>
