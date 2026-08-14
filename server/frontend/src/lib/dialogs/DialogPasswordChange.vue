<script setup>
import { computed, ref, watch } from 'vue'

import FloatLabel from 'primevue/floatlabel'
import Dialog from 'primevue/dialog'
import Password from 'primevue/password'
import Button from 'primevue/button'
import Message from 'primevue/message'

import { useApp } from '@/stores'
import { getApiErrorMessage } from '@/api/utils'
import { usePasswordChangeForm } from '@/lib/passwordChange'

const app = useApp()

const visible = defineModel('visible')

const form = usePasswordChangeForm(() => ({
  email: app.auth.user?.email,
  username: app.auth.user?.username
}))

const busy = ref(false)
const serverError = ref(null)

watch(visible, () => {
  form.reset()
  serverError.value = null
  busy.value = false
})

const message = computed(() => {
  if (form.policyError.value) return form.policyError.value
  if (form.sameAsCurrent.value) return 'Your new password must differ from your current one.'
  if (form.mismatch.value) return 'Your passwords do not match.'
  return serverError.value
})

const execute = async () => {
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
    // Stay open on failure: closing would discard what the user typed and hide
    // why it was refused.
    serverError.value = getApiErrorMessage(error, 'Could not change your password.')
    busy.value = false
    return
  }
  busy.value = false
  visible.value = false
}
</script>

<template>
  <Dialog v-model:visible="visible" header="Change your password" modal style="width: 400px">
    <section>
      <FloatLabel>
        <Password id="current-password" v-model="form.password.current" fluid />
        <label for="current-password">Current password</label>
      </FloatLabel>
      <FloatLabel>
        <Password
          id="new-password"
          v-model="form.password.new"
          :invalid="!!form.policyError.value || form.sameAsCurrent.value"
          fluid
        />
        <label for="new-password">New password</label>
      </FloatLabel>
      <FloatLabel>
        <Password
          id="new-password-verify"
          v-model="form.password.verify"
          :invalid="form.mismatch.value"
          fluid
          @keyup.enter="execute"
        />
        <label for="new-password-verify">Verify new password</label>
      </FloatLabel>
    </section>
    <menu style="margin-top: 2rem">
      <Message v-if="message" icon="pi pi-exclamation-triangle" severity="secondary">
        {{ message }}
      </Message>
      <Button label="Cancel" @click="visible = false" severity="secondary" :disabled="busy" />
      <Button label="Save" @click="execute" :disabled="form.invalid.value || busy" />
    </menu>
  </Dialog>
</template>
