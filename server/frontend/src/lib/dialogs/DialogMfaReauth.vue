<script setup>
import { ref, watch } from 'vue'

import Dialog from 'primevue/dialog'
import FloatLabel from 'primevue/floatlabel'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import Message from 'primevue/message'

import { api } from '@/api'
import { getApiErrorMessage } from '@/api/utils'

// Prompt shown when an action needs a freshly presented code. Emits `verified`
// once the server accepts one; the caller retries whatever it was doing.
const visible = defineModel('visible')
const emit = defineEmits(['verified'])

const code = ref('')
const busy = ref(false)
const error = ref(null)

watch(visible, () => {
  code.value = ''
  busy.value = false
  error.value = null
})

const submit = async () => {
  if (!code.value.trim() || busy.value) return
  busy.value = true
  error.value = null
  try {
    await api.http.post(
      '/auth/mfa/reauth',
      { code: code.value },
      { use: 'read', type: 'mfa_reauth', errors: 'inline' }
    )
  } catch (e) {
    error.value = getApiErrorMessage(e, 'That code is not valid. Please try again.')
    code.value = ''
    busy.value = false
    return
  }
  busy.value = false
  visible.value = false
  emit('verified')
}
</script>

<template>
  <Dialog v-model:visible="visible" modal header="Confirm it's you" :style="{ width: '26rem' }">
    <p>
      This action creates a long-lived access credential, so it needs a current code from your
      authenticator app.
    </p>
    <Message v-if="error" severity="error" style="margin-top: 1rem">{{ error }}</Message>
    <FloatLabel style="margin-top: 1.5rem">
      <InputText
        id="mfa-reauth-code"
        v-model="code"
        inputmode="numeric"
        autocomplete="one-time-code"
        autofocus
        fluid
        @keyup.enter="submit"
      />
      <label for="mfa-reauth-code">Verification or recovery code</label>
    </FloatLabel>
    <template #footer>
      <Button label="Cancel" severity="secondary" text @click="visible = false" />
      <Button label="Confirm" :disabled="!code.trim() || busy" :loading="busy" @click="submit" />
    </template>
  </Dialog>
</template>
