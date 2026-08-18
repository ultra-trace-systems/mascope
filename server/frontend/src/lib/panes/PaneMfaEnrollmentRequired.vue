<script setup>
import { computed, ref, watch } from 'vue'

import QRCode from 'qrcode'

import FloatLabel from 'primevue/floatlabel'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import Message from 'primevue/message'

import { api } from '@/api'
import { getApiErrorMessage } from '@/api/utils'
import { useApp } from '@/stores'
import { BaseCopyableField } from '@/lib/base'

// The enrolment screen shown to an account the deployment requires to hold a
// second factor. Distinct from the settings dialog: there enrolment is
// optional and cancellable, here it is the only way into the application, so
// there is no cancel and the recovery codes are acknowledged before the app
// opens.
const app = useApp()

const STEPS = { intro: 'intro', confirm: 'confirm', codes: 'codes' }

const step = ref(STEPS.intro)
const secret = ref(null)
const qr = ref(null)
const code = ref('')
const recoveryCodes = ref([])
const acknowledged = ref(false)
const busy = ref(false)
const error = ref(null)
const unavailable = ref(false)

// Reset if the account behind the screen changes (a sign-out and a different
// sign-in without a reload).
watch(
  () => app.auth.user?.id,
  () => {
    step.value = STEPS.intro
    secret.value = null
    qr.value = null
    code.value = ''
    recoveryCodes.value = []
    acknowledged.value = false
    error.value = null
  }
)

const start = async () => {
  if (busy.value) return
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
    // 503 means the deployment has no MFA key, so nobody can enrol. Say so
    // plainly rather than looping the user through a button that cannot work.
    unavailable.value = e?.response?.status === 503
    error.value = getApiErrorMessage(e, 'Could not start setup. Please try again.')
  }
  busy.value = false
}

const confirm = async () => {
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

const downloadCodes = () => {
  const url = URL.createObjectURL(new Blob([codesText.value], { type: 'text/plain' }))
  const link = document.createElement('a')
  link.href = url
  link.download = 'mascope-recovery-codes.txt'
  link.click()
  URL.revokeObjectURL(url)
}

// Re-reading the profile is what clears the gate: the requirement is derived
// server-side from the account's role and its factor, so the app opens as soon
// as /users/me stops reporting it.
const enterApp = async () => {
  busy.value = true
  try {
    await app.auth.identify()
  } catch {
    error.value = 'Two-factor authentication is on. Reloading...'
    setTimeout(() => window.location.reload(), 1500)
    return
  }
  busy.value = false
}
</script>

<template>
  <h3 style="opacity: 0.7; text-align: center">Set up two-factor authentication</h3>

  <Message v-if="unavailable" severity="error" icon="pi pi-exclamation-triangle">
    This server requires two-factor authentication but is not configured for it. Ask an
    administrator to set up the two-factor encryption key.
  </Message>

  <template v-else>
    <Message
      v-if="step === STEPS.intro"
      severity="secondary"
      icon="pi pi-shield"
      style="margin-bottom: 1.5rem"
    >
      This account requires two-factor authentication. You will need an authenticator app on your
      phone.
    </Message>
    <Message v-if="error" severity="error" style="margin-bottom: 1rem">{{ error }}</Message>

    <!-- Step 1 -->
    <template v-if="step === STEPS.intro">
      <Button
        label="Start setup"
        icon="pi pi-qrcode"
        :loading="busy"
        :disabled="busy"
        fluid
        @click="start"
      />
    </template>

    <!-- Step 2 -->
    <template v-else-if="step === STEPS.confirm">
      <p>Scan this with your authenticator app, then enter the code it shows.</p>
      <div style="display: flex; justify-content: center; margin: 1rem 0">
        <img v-if="qr" :src="qr" alt="Two-factor setup QR code" width="220" height="220" />
      </div>
      <p style="opacity: 0.7; font-size: 0.85rem">Can't scan? Enter this key by hand:</p>
      <BaseCopyableField v-if="secret" :field="secret" />
      <FloatLabel style="margin-top: 1.5rem">
        <InputText
          id="mfa-required-code"
          v-model="code"
          inputmode="numeric"
          autocomplete="one-time-code"
          autofocus
          fluid
          @keyup.enter="confirm"
        />
        <label for="mfa-required-code">Verification code</label>
      </FloatLabel>
      <Button
        label="Verify and turn on"
        :disabled="!code.trim() || busy"
        :loading="busy"
        fluid
        style="margin-top: 1.5rem"
        @click="confirm"
      />
    </template>

    <!-- Step 3 -->
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
      <label style="display: flex; gap: 0.5rem; align-items: center; margin-top: 1rem">
        <input type="checkbox" v-model="acknowledged" />
        <span>I have saved these codes</span>
      </label>
      <Button
        label="Continue to Mascope"
        icon="pi pi-arrow-right"
        :disabled="!acknowledged || busy"
        :loading="busy"
        fluid
        style="margin-top: 1.5rem"
        @click="enterApp"
      />
    </template>
  </template>
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
