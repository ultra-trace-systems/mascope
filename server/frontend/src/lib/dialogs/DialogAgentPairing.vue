<script setup>
import { ref, computed, watch } from 'vue'

import FloatLabel from 'primevue/floatlabel'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import Message from 'primevue/message'

import { api } from '@/api'
import { needsMfaReauth } from '@/api/utils'

import DialogMfaReauth from './DialogMfaReauth.vue'

const SERVICE_LABELS = {
  'tof-agent': 'TOF Agent',
  'file-agent': 'File Agent',
  'export-agent': 'CSV Export Agent'
}

const visible = defineModel('visible')

const code = ref(null)
const busy = ref(false)
const error = ref(null)
const approved = ref(null)
// Approving mints a year-long agent credential, so the server may ask for a
// current code first. The prompt sits inside this dialog rather than around
// it: approving is the only call here that can be refused that way.
const reauthVisible = ref(false)

watch(visible, () => {
  code.value = null
  busy.value = false
  error.value = null
  approved.value = null
  reauthVisible.value = false
})

// Codes look like ABC-123; accept letters/digits with optional dash
const invalidCode = computed(
  () => !code.value || code.value.replace(/[^a-zA-Z0-9]/g, '').length < 6
)

const approve = async (mayPrompt = true) => {
  busy.value = true
  error.value = null
  try {
    const response = await api.http.post('/auth/pairing/approve', {
      user_code: code.value
    })
    approved.value = response?.data
  } catch (e) {
    if (needsMfaReauth(e) && mayPrompt) {
      // Not a failure: the pairing code is still good, the user just has to
      // confirm they hold the factor. Approval retries once they have.
      reauthVisible.value = true
      busy.value = false
      return
    }
    error.value =
      e?.response?.data?.detail || e?.response?.data?.error || e?.message || 'Approval failed.'
  } finally {
    busy.value = false
  }
}

// The retry may not prompt again: the code was just accepted, so a refusal
// here is a real failure and belongs in `error` rather than in another prompt.
const onReauthVerified = () => approve(false)
</script>

<template>
  <Dialog v-model:visible="visible" header="Pair an agent" modal style="width: 400px">
    <section v-if="!approved">
      <p>
        Enter the pairing code shown in the agent's console window. Approving creates an access
        token for the agent under your account, without affecting existing tokens.
      </p>
      <FloatLabel style="margin-top: 1.5rem">
        <InputText
          id="pairing-code"
          v-model="code"
          fluid
          style="text-transform: uppercase"
          @keyup.enter="!invalidCode && !busy && approve()"
        />
        <label for="pairing-code">Pairing code (e.g. ABC-123)</label>
      </FloatLabel>
    </section>
    <section v-else>
      <Message icon="pi pi-check-circle" severity="success">
        Paired {{ SERVICE_LABELS[approved.service_name] || approved.service_name
        }}<template v-if="approved.machine_name"> on {{ approved.machine_name }}</template
        >. The agent will receive its token within a few seconds.
      </Message>
    </section>
    <menu style="margin-top: 2rem">
      <Message v-if="error" icon="pi pi-exclamation-triangle" severity="secondary">
        {{ error }}
      </Message>
      <template v-if="!approved">
        <Button label="Cancel" @click="visible = false" severity="secondary" />
        <!-- Called through a wrapper so the click event is not passed as the
             may-prompt argument. -->
        <Button label="Approve" @click="() => approve()" :disabled="invalidCode" :loading="busy" />
      </template>
      <Button v-else label="Done" @click="visible = false" />
    </menu>
  </Dialog>
  <DialogMfaReauth v-model:visible="reauthVisible" @verified="onReauthVerified" />
</template>
