<script setup>
import FloatLabel from 'primevue/floatlabel'
import InputText from 'primevue/inputtext'
import Password from 'primevue/password'
import Button from 'primevue/button'
import Message from 'primevue/message'

import { useAuth } from '@/stores/auth'
import { getApiErrorMessage } from '@/api/utils'

import { computed, reactive, ref, watch } from 'vue'

const auth = useAuth()

const input = reactive({
  email: null,
  password: null
})

const invalid = computed(() => ({
  email: input.email?.length < 5 || !input.email?.includes('@'),
  password: !(input.password?.length > 0)
}))

const disabled = computed(() => invalid.value.email || invalid.value.password)

// --- Verification code step ---

const code = ref('')
const useRecoveryCode = ref(false)
const busy = ref(false)
const serverError = ref(null)

// The password is not kept across the step change: once the server holds the
// half-finished sign-in, nothing here needs it again, and a rejected code must
// not leave it sitting in memory to be retried.
watch(
  () => auth.mfaPending,
  (pending) => {
    if (pending) {
      input.password = null
    }
    code.value = ''
    useRecoveryCode.value = false
    serverError.value = null
  }
)

const submitCode = async () => {
  if (!code.value.trim() || busy.value) return
  busy.value = true
  serverError.value = null
  try {
    await auth.verifyMfa(code.value)
  } catch (error) {
    // A 401 means the attempt itself expired; the store has already sent us
    // back to the credentials step, where this message no longer applies.
    if (error?.response?.status !== 401) {
      serverError.value = getApiErrorMessage(error, 'That code is not valid. Please try again.')
    }
    code.value = ''
    busy.value = false
    return
  }
  busy.value = false
}
</script>

<template>
  <!-- Verification step. Reached only once the password has been accepted; no
       session exists until the code is verified. -->
  <div v-if="auth.mfaPending" class="fields" style="flex-flow: column">
    <h3 style="opacity: 0.7">Two-factor verification</h3>
    <Message severity="secondary" icon="pi pi-shield" style="margin-bottom: 1.5rem">
      {{
        useRecoveryCode
          ? 'Enter one of the recovery codes you saved when you set up two-factor authentication.'
          : 'Enter the 6-digit code from your authenticator app.'
      }}
    </Message>
    <FloatLabel>
      <InputText
        id="login-mfa-code"
        v-model="code"
        :invalid="!!serverError"
        :inputmode="useRecoveryCode ? 'text' : 'numeric'"
        autocomplete="one-time-code"
        autofocus
        fluid
        required
        @keyup.enter="submitCode"
      />
      <label for="login-mfa-code">{{
        useRecoveryCode ? 'Recovery code' : 'Verification code'
      }}</label>
    </FloatLabel>
    <Message v-if="serverError" severity="error" style="margin-top: 1rem">
      {{ serverError }}
    </Message>
    <Button
      @click="submitCode"
      label="Verify"
      icon="pi pi-sign-in"
      :disabled="!code.trim() || busy"
      :loading="busy"
      style="margin-top: 2rem"
    />
    <Button
      @click="useRecoveryCode = !useRecoveryCode"
      :label="useRecoveryCode ? 'Use your authenticator app' : 'Use a recovery code'"
      severity="secondary"
      variant="text"
      size="small"
    />
    <Button
      @click="auth.cancelMfa()"
      label="Cancel"
      severity="secondary"
      variant="text"
      size="small"
    />
  </div>

  <!-- Credentials step -->
  <div v-else class="fields" style="flex-flow: column">
    <h3 style="opacity: 0.7">Sign-in to Mascope</h3>
    <FloatLabel>
      <InputText
        id="login-email"
        v-model="input.email"
        :invalid="input.email && invalid.email"
        fluid
        required
      />
      <label for="login-email">Email</label>
    </FloatLabel>
    <FloatLabel>
      <Password
        id="login-password"
        v-model="input.password"
        :invalid="input.password && invalid.password"
        fluid
        required
        @keyup.enter="
          () => {
            if (!disabled) {
              auth.login(input)
            }
          }
        "
      />
      <label for="login-password">Password</label>
    </FloatLabel>
    <Button
      @click="auth.login(input)"
      label="Login"
      icon="pi pi-sign-in"
      :disabled="disabled"
      style="margin-top: 2rem"
    />
  </div>
</template>
