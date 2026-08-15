<script setup>
import { computed, ref } from 'vue'

import FloatLabel from 'primevue/floatlabel'
import Password from 'primevue/password'
import Button from 'primevue/button'
import Message from 'primevue/message'

import { useApp } from '@/stores'
import { getApiErrorMessage } from '@/api/utils'
import { usePasswordChangeForm } from '@/lib/passwordChange'

const app = useApp()

const form = usePasswordChangeForm(() => ({
  email: app.auth.user?.email,
  username: app.auth.user?.username
}))

const busy = ref(false)
const serverError = ref(null)

// Stated as a property of the system, or of how the account was set up - never
// of the person reading it.
const REASONS = {
  // Deliberately does not name a cause. An administrator can require this for
  // any reason - a tightened policy, a periodic refresh, a credential concern -
  // and the application is not told which, so claiming one would eventually be
  // false.
  policy:
    'A password change has been requested by an administrator. Set a new password to continue.',
  reset:
    'You were given a temporary password by an administrator. Choose your own password to continue.',
  new_account:
    'Welcome to Mascope. Your account was created with a password someone else chose - set your own to continue.'
}
const reason = computed(
  () =>
    REASONS[app.auth.passwordChangeReason] ??
    'Your account needs a new password before you can continue.'
)

const message = computed(() => {
  if (form.policyError.value) return form.policyError.value
  if (form.sameAsCurrent.value) return 'Your new password must differ from your current one.'
  if (form.mismatch.value) return 'Your passwords do not match.'
  return serverError.value
})

const submit = async () => {
  if (form.invalid.value || busy.value) return
  busy.value = true
  serverError.value = null
  try {
    await app.data.user.updateMeCreds({
      currentPassword: form.password.current,
      newPassword: form.password.new,
      verifyNewPassword: form.password.verify
    })
  } catch (error) {
    // Nothing is cleared: making someone retype three fields after a rejection
    // wastes their time and their rate-limit budget.
    serverError.value = getApiErrorMessage(
      error,
      'Could not set the new password. Please try again.'
    )
    busy.value = false
    return
  }
  // Outside the try above on purpose: the password has already been changed by
  // this point, so a failure here must not read as a failed submit - the old
  // password no longer works, and a retry would be refused.
  try {
    await app.auth.identify()
  } catch {
    serverError.value = 'Your password was changed. Reloading...'
    setTimeout(() => window.location.reload(), 1500)
  }
  busy.value = false
}
</script>

<template>
  <h3 style="opacity: 0.7; text-align: center">Set a new password</h3>
  <Message severity="secondary" icon="pi pi-info-circle" style="margin-bottom: 1.5rem">
    {{ reason }}
  </Message>
  <!-- One field per row: .fields wraps into columns, which reads as three
       unrelated inputs rather than one ordered sequence. -->
  <div class="fields" style="flex-flow: column">
    <div class="field">
      <FloatLabel>
        <Password id="current-password" v-model="form.password.current" fluid required />
        <label for="current-password">Current password</label>
      </FloatLabel>
    </div>
    <div class="field">
      <FloatLabel>
        <Password
          id="new-password"
          v-model="form.password.new"
          :invalid="!!form.policyError.value || form.sameAsCurrent.value"
          fluid
          required
        />
        <label for="new-password">New password</label>
      </FloatLabel>
    </div>
    <div class="field">
      <FloatLabel>
        <Password
          id="new-password-verify"
          v-model="form.password.verify"
          :invalid="form.mismatch.value"
          fluid
          required
          @keyup.enter="submit"
        />
        <label for="new-password-verify">Verify new password</label>
      </FloatLabel>
    </div>
  </div>

  <!-- Until something is typed the rules are requirements, not results: ticking
       them off against an empty field would claim the user had already met
       them. -->
  <ul class="policy-checks">
    <li
      v-for="check in form.checks.value"
      :key="check.id"
      :class="{ ok: check.ok && !!form.password.new }"
    >
      <i :class="check.ok && !!form.password.new ? 'pi pi-check' : 'pi pi-circle'" />
      <span>{{ check.label }}</span>
    </li>
  </ul>

  <Message v-if="message" icon="pi pi-exclamation-triangle" severity="secondary">
    {{ message }}
  </Message>

  <Button
    @click="submit"
    label="Set password"
    icon="pi pi-check"
    :disabled="form.invalid.value || busy"
    :loading="busy"
    fluid
    style="margin-top: 2rem"
  />
  <Button
    @click="app.auth.logout"
    label="Log out"
    severity="secondary"
    text
    fluid
    :disabled="busy"
    style="margin-top: 0.5rem"
  />
  <p class="token-note">
    Your API access tokens (SDK, notebooks, instrument agents) are replaced when your password
    changes, and will need to be regenerated or re-paired.
  </p>
</template>

<style scoped>
.policy-checks {
  list-style: none;
  padding: 0;
  margin: 1rem 0;
  font-size: 0.9em;
  opacity: 0.75;
}

.policy-checks li {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.15rem 0;
}

.policy-checks li.ok {
  opacity: 0.6;
}

.token-note {
  margin-top: 1.5rem;
  font-size: 0.8em;
  opacity: 0.6;
  text-align: center;
}
</style>
