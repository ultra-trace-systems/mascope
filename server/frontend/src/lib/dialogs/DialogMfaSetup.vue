<script setup>
import { computed, ref, watch } from 'vue'

import QRCode from 'qrcode'

import Dialog from 'primevue/dialog'
import FloatLabel from 'primevue/floatlabel'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import Message from 'primevue/message'

import { api } from '@/api'
import { getApiErrorMessage } from '@/api/utils'
import { useApp } from '@/stores'
import { BaseCopyableField } from '@/lib/base'

const visible = defineModel('visible')
const app = useApp()

// scan -> confirm the first code -> write the recovery codes down. The last
// step is not skippable: the codes are shown exactly once, and a user who
// closes the dialog before reading them has no fallback if the phone is lost.
const STEPS = { scan: 'scan', confirm: 'confirm', codes: 'codes' }

const status = ref(null)
const step = ref(STEPS.scan)
const secret = ref(null)
const qr = ref(null)
const code = ref('')
const recoveryCodes = ref([])
const acknowledged = ref(false)
const busy = ref(false)
const error = ref(null)

const enabled = computed(() => !!status.value?.enabled)
const available = computed(() => !!status.value?.available)
// The deployment requires a factor at this account's role. The server
// refuses to turn one off in that case, so the button is not offered.
const required = computed(() => !!status.value?.required)
const policyRole = computed(() => status.value?.policy_min_role ?? null)

const reset = () => {
  step.value = STEPS.scan
  secret.value = null
  qr.value = null
  code.value = ''
  recoveryCodes.value = []
  acknowledged.value = false
  busy.value = false
  error.value = null
}

const loadStatus = async () => {
  try {
    status.value = await api.http.get('/auth/mfa/status', {
      use: 'read',
      type: 'read_mfa_status'
    })
  } catch (e) {
    error.value = getApiErrorMessage(e, 'Could not read two-factor status.')
  }
}

watch(visible, async (open) => {
  if (!open) {
    reset()
    return
  }
  reset()
  await loadStatus()
})

const startEnrollment = async () => {
  busy.value = true
  error.value = null
  try {
    const data = await api.http.post(
      '/auth/mfa/enroll',
      {},
      { use: 'read', type: 'mfa_enroll', errors: 'inline' }
    )
    secret.value = data.secret
    qr.value = await QRCode.toDataURL(data.provisioning_uri, { margin: 1, width: 220 })
    step.value = STEPS.confirm
  } catch (e) {
    error.value = getApiErrorMessage(e, 'Could not start setup. Please try again.')
  }
  busy.value = false
}

const confirmEnrollment = async () => {
  if (!code.value.trim() || busy.value) return
  busy.value = true
  error.value = null
  try {
    const data = await api.http.post(
      '/auth/mfa/enroll/confirm',
      { code: code.value },
      { use: 'read', type: 'mfa_enroll_confirm', errors: 'inline' }
    )
    recoveryCodes.value = data.recovery_codes
    step.value = STEPS.codes
    await loadStatus()
  } catch (e) {
    error.value = getApiErrorMessage(e, 'That code is not valid. Please try again.')
    code.value = ''
  }
  busy.value = false
}

const disable = async () => {
  if (!code.value.trim() || busy.value) return
  busy.value = true
  error.value = null
  try {
    await api.http.delete('/auth/mfa', {
      data: { code: code.value },
      use: 'delete',
      type: 'mfa_disable',
      errors: 'inline'
    })
    await loadStatus()
    code.value = ''
  } catch (e) {
    error.value = getApiErrorMessage(e, 'That code is not valid. Please try again.')
    code.value = ''
  }
  busy.value = false
}

const codesText = computed(() =>
  [
    'Mascope two-factor recovery codes',
    `Account: ${app.auth.user?.email ?? ''}`,
    'Each code works once. Keep them somewhere safe and offline.',
    '',
    ...recoveryCodes.value
  ].join('\n')
)

// Written through a Blob rather than a data: URL so the whole list is not
// carried in the DOM, and so the filename survives.
const downloadCodes = () => {
  const url = URL.createObjectURL(new Blob([codesText.value], { type: 'text/plain' }))
  const link = document.createElement('a')
  link.href = url
  link.download = 'mascope-recovery-codes.txt'
  link.click()
  URL.revokeObjectURL(url)
}

const close = () => {
  visible.value = false
}
</script>

<template>
  <!-- Both flags, not just :closable - PrimeVue's Escape handler never consults
       closable, and closing here runs reset(), which drops recovery codes the
       server cannot reissue. The keydown listener is bound once, when the
       dialog opens, so this dialog must never open already on the codes step. -->
  <Dialog
    v-model:visible="visible"
    modal
    header="Two-factor authentication"
    :closable="step !== STEPS.codes"
    :closeOnEscape="step !== STEPS.codes"
    :style="{ width: '30rem' }"
  >
    <Message v-if="error" severity="error" style="margin-bottom: 1rem">{{ error }}</Message>

    <!-- Deployment has no MFA key, so seeds cannot be stored at all. -->
    <template v-if="status && !available && !enabled">
      <Message severity="warn" icon="pi pi-exclamation-triangle">
        Two-factor authentication is not configured on this server. Ask an administrator to set it
        up.
      </Message>
    </template>

    <!-- Already on: offer to turn it off, which needs a current code. -->
    <template v-else-if="enabled && step === STEPS.scan">
      <Message severity="success" icon="pi pi-shield" style="margin-bottom: 1rem">
        Two-factor authentication is on for this account.
      </Message>
      <p v-if="status?.unused_recovery_codes != null" style="opacity: 0.7">
        {{ status.unused_recovery_codes }} recovery code(s) remaining.
      </p>
      <Message v-if="required" severity="secondary" icon="pi pi-lock" style="margin-top: 1rem">
        This server requires two-factor authentication for
        {{ policyRole ? `${policyRole} accounts and above` : 'this account' }}, so it cannot be
        turned off here. An administrator can reset it if you lose your authenticator.
      </Message>
      <template v-else>
        <p style="margin-top: 1rem">Enter a current code to turn it off.</p>
        <FloatLabel style="margin-top: 1.5rem">
          <InputText
            id="mfa-disable-code"
            v-model="code"
            inputmode="numeric"
            autocomplete="one-time-code"
            fluid
            @keyup.enter="disable"
          />
          <label for="mfa-disable-code">Verification or recovery code</label>
        </FloatLabel>
      </template>
    </template>

    <!-- Step 1: hand over the seed. -->
    <template v-else-if="step === STEPS.scan">
      <p>
        Two-factor authentication asks for a code from your phone in addition to your password, so a
        stolen password is not enough to sign in.
      </p>
      <Message v-if="required" severity="warn" icon="pi pi-exclamation-triangle">
        This server requires it for
        {{ policyRole ? `${policyRole} accounts and above` : 'this account' }}. You will be asked to
        set it up at your next sign-in.
      </Message>
    </template>

    <!-- Step 2: scan and confirm. -->
    <template v-else-if="step === STEPS.confirm">
      <p>Scan this with your authenticator app, then enter the code it shows.</p>
      <div style="display: flex; justify-content: center; margin: 1rem 0">
        <img v-if="qr" :src="qr" alt="Two-factor setup QR code" width="220" height="220" />
      </div>
      <p style="opacity: 0.7; font-size: 0.85rem">Can't scan? Enter this key by hand:</p>
      <BaseCopyableField v-if="secret" :field="secret" />
      <FloatLabel style="margin-top: 1.5rem">
        <InputText
          id="mfa-confirm-code"
          v-model="code"
          inputmode="numeric"
          autocomplete="one-time-code"
          fluid
          @keyup.enter="confirmEnrollment"
        />
        <label for="mfa-confirm-code">Verification code</label>
      </FloatLabel>
    </template>

    <!-- Step 3: the codes, shown once. -->
    <template v-else-if="step === STEPS.codes">
      <Message severity="success" icon="pi pi-check" style="margin-bottom: 1rem">
        Two-factor authentication is on.
      </Message>
      <p>
        Save these recovery codes now. Each one works once, and they are the only way back in if you
        lose your phone. <strong>They are not shown again.</strong>
      </p>
      <ul id="recovery-codes">
        <li v-for="c in recoveryCodes" :key="c">{{ c }}</li>
      </ul>
      <Button
        label="Download codes"
        icon="pi pi-download"
        severity="secondary"
        text
        @click="downloadCodes"
      />
      <div style="margin-top: 1rem">
        <label style="display: flex; gap: 0.5rem; align-items: center">
          <input type="checkbox" v-model="acknowledged" />
          <span>I have saved these codes</span>
        </label>
      </div>
    </template>

    <template #footer>
      <!-- One footer for every step: a slot has to be a direct child of the
           Dialog, so the branching lives inside it rather than around it. -->
      <template v-if="status && !available && !enabled">
        <Button label="Close" severity="secondary" text @click="close" />
      </template>
      <template v-else-if="enabled && step === STEPS.scan">
        <Button label="Close" severity="secondary" text @click="close" />
        <Button
          v-if="!required"
          label="Turn off"
          severity="danger"
          :disabled="!code.trim() || busy"
          :loading="busy"
          @click="disable"
        />
      </template>
      <template v-else-if="step === STEPS.scan">
        <Button label="Cancel" severity="secondary" text @click="close" />
        <Button label="Set up" :loading="busy" :disabled="busy" @click="startEnrollment" />
      </template>
      <template v-else-if="step === STEPS.confirm">
        <Button label="Cancel" severity="secondary" text @click="close" />
        <Button
          label="Verify and turn on"
          :disabled="!code.trim() || busy"
          :loading="busy"
          @click="confirmEnrollment"
        />
      </template>
      <template v-else-if="step === STEPS.codes">
        <Button label="Done" :disabled="!acknowledged" @click="close" />
      </template>
    </template>
  </Dialog>
</template>

<style scoped>
#recovery-codes {
  list-style: none;
  padding: 0.75rem;
  margin: 1rem 0;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0.35rem;
  font-family: monospace;
  font-size: 1rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
}
</style>
