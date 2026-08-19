<script setup>
import { ref, computed, watch } from 'vue'

import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Select from 'primevue/select'
import InputText from 'primevue/inputtext'
import FloatLabel from 'primevue/floatlabel'
import Message from 'primevue/message'
import { useConfirm } from 'primevue/useconfirm'

import { api } from '@/api'
import { useApp } from '@/stores'
import { BaseCopyableField } from '@/lib/base'
import { roles, prettyRoleName, roleLevel, ROLES } from '@/lib/roles'

const app = useApp()
const confirm = useConfirm()

const visible = defineModel('visible')

const close = () => {
  visible.value = false
}

const edited = ref(null)
const created = ref(null)
const password = ref(null)
// The temporary password the server generated for a new account, held until
// the administrator has copied it. Shown once and unrecoverable afterwards -
// same contract as a password reset.
const createdPassword = ref(null)

// The deployment's two-factor policy, read from the caller's own status route -
// it reports the policy alongside the account state. Shown so an owner can see
// what is in force without reading the server's config file; changing it is an
// operator action on the host, deliberately not a control here.
const mfaPolicy = ref(null)
const loadMfaPolicy = async () => {
  try {
    mfaPolicy.value = await api.http.get('/auth/mfa/status', {
      use: 'read',
      type: 'read_mfa_status'
    })
  } catch {
    // A policy we cannot read is not worth interrupting user management for;
    // the line simply does not render.
    mfaPolicy.value = null
  }
}

const reset = () => {
  edited.value = null
  created.value = null
  password.value = null
  createdPassword.value = null
}
const editing = ({ id }) => id == edited.value?.id

// Whether the signed-in user may clear this account's second factor - the same
// rule the server enforces, so the button is offered only where a click can
// succeed: owners for anyone but themselves, admins for guests and editors.
const canResetMfa = (target) =>
  !!target.mfa_enabled &&
  target.id != app.auth.user.id &&
  (app.auth.user.role_name == 'owner' ||
    (app.auth.user.role_name == 'admin' && roleLevel(target.role_name) < ROLES.admin))

const user = {
  edit: ({ id, username, email, role_id }) => {
    created.value = null
    edited.value = {
      id,
      username,
      email,
      role_id
    }
  },
  create: () => {
    edited.value = null
    createdPassword.value = null
    created.value = {
      username: null,
      email: null,
      role_id: 100
    }
  },
  cancel: reset,
  save: async () => {
    if (edited.value) {
      await app.data.user.update(edited.value)
      reset()
      return
    }
    if (created.value) {
      const response = await app.data.user.create(created.value)
      // Clear the form but hold the view open on the password: resetting here
      // would discard the one copy of it that exists.
      created.value = null
      createdPassword.value = response?.temporary_password ?? null
      if (!createdPassword.value) reset()
    }
  },
  delete: (data) => {
    confirm.require({
      icon: 'pi pi-exclamation-triangle',
      header: 'Remove user',
      message: `Are you sure you want to remove the ${data.role_name} user ${data.username} (${data.email})?`,
      accept: () => {
        app.data.user.delete(data)
      },
      acceptProps: {
        icon: 'pi pi-trash',
        label: 'Delete',
        severity: 'danger'
      },
      rejectProps: {
        icon: 'pi pi-times',
        label: 'Cancel',
        severity: 'secondary'
      }
    })
  },
  resetPassword: async (data) => {
    password.value = (await app.data.user.resetPassword(data)).new_password
  },
  resetMfa: (data) => {
    confirm.require({
      icon: 'pi pi-shield',
      header: 'Reset two-factor authentication',
      message:
        `Clear two-factor authentication for ${data.username} (${data.email})? ` +
        'They keep their password and set up a new authenticator themselves. If this ' +
        'deployment requires a second factor for their role, they are held at the setup ' +
        'screen until they do.',
      accept: async () => {
        // PrimeVue does not await this callback; a rejection would otherwise be
        // unhandled. The http layer already raised the error notification.
        try {
          await app.data.user.resetMfa(data)
        } catch {
          return
        }
      },
      acceptProps: {
        icon: 'pi pi-replay',
        label: 'Reset two-factor',
        severity: 'danger'
      },
      rejectProps: {
        icon: 'pi pi-times',
        label: 'Cancel',
        severity: 'secondary'
      }
    })
  },
  requirePasswordChange: () => {
    confirm.require({
      icon: 'pi pi-exclamation-triangle',
      header: 'Require a password change',
      message:
        'Every Mascope account - including your own - will be asked to set a new password. ' +
        'Everyone keeps signing in with their current password until they change it. Once a ' +
        'user changes theirs, their API access tokens (SDK, notebooks, instrument agents) stop ' +
        'working and must be regenerated or re-paired. You will be asked to set yours ' +
        'immediately. Only a server administrator can reverse this.',
      accept: async () => {
        // PrimeVue does not await this callback, so a rejection here would be
        // unhandled - and the dialog would sit open with nothing explaining
        // that nothing happened.
        try {
          await app.data.user.requirePasswordChange()
        } catch {
          // The http layer already raised the error notification; keep the
          // dialog open so the owner can retry or close it themselves.
          return
        }
        close()
        // The owner is included, so the password screen replaces this view.
        // Deliberately no user list reload: this account is already required to
        // change its password, so the request would be refused. A failed
        // profile re-read is caught: the gate intercepts the next request
        // anyway.
        await app.auth.identify().catch(() => {})
      },
      acceptProps: {
        icon: 'pi pi-key',
        label: 'Require password change',
        severity: 'danger'
      },
      rejectProps: {
        icon: 'pi pi-times',
        label: 'Cancel',
        severity: 'secondary'
      }
    })
  }
}

const isMe = (user) => user.id == app.auth.user.id

const canModify = (role_id) => {
  const self = app.auth.user
  if (self.role_name == 'owner') {
    return true
  } else if (self.role_name == 'admin') {
    return role_id < self.role_id
  }
}

const selectableRoles = computed(() => roles.filter(({ value }) => canModify(value)))

const invalidCreated = computed(() => {
  const email =
    !created.value?.email ||
    created.value?.email?.length < 5 ||
    !created.value?.email?.includes('@')
  const username = !created.value?.username || created.value?.username?.length < 5
  return {
    email,
    username,
    form: email || username
  }
})

watch(visible, (open) => {
  reset()
  if (open) loadMfaPolicy()
})
</script>

<template>
  <Dialog v-model:visible="visible" header="Manage users" modal style="width: 800px" closable>
    <section>
      <DataTable
        :value="
          app.data.user.list.map((data) => ({
            pretty_role_name: prettyRoleName(data),
            ...data
          }))
        "
        sortField="role_id"
        :sortOrder="-1"
        scrollable
        scrollHeight="400px"
      >
        <Column header="Username" field="username">
          <template #body="{ data }">
            <InputText v-if="editing(data)" v-model="edited.username" />
            <span v-else>{{ data.username }}</span>
          </template>
        </Column>
        <Column header="Email" field="email">
          <template #body="{ data }">
            <div v-if="editing(data) && !password" class="row" style="width: min-content">
              <InputText
                v-model="edited.email"
                :disabled="data.id == app.auth.user.id"
                v-tooltip.bottom="
                  data.id == app.auth.user.id
                    ? 'You cannot edit your own email'
                    : 'Enter a new email address'
                "
              />
              <Button
                v-tooltip.bottom="'Reset password (issues a temporary one)'"
                icon="pi pi-key"
                severity="secondary"
                text
                @click="() => user.resetPassword(data)"
              />
            </div>
            <div
              v-else-if="editing(data) && password"
              class="col"
              style="gap: 0; width: min-content; align-items: flex-start"
            >
              <span style="text-align: left; font-size: smaller; opacity: 0.7"
                >Temporary password:</span
              >
              <BaseCopyableField :field="password" @copy="reset" />
              <span class="temporary-password-note">
                Share it with {{ data.username }} - they must set their own password at next sign
                in.
              </span>
            </div>
            <span v-else>{{ data.email }}</span>
          </template>
        </Column>
        <Column header="Two-factor">
          <template #body="{ data }">
            <div class="row" style="gap: 0.35rem; align-items: center">
              <span
                :class="data.mfa_enabled ? 'pi pi-shield' : 'pi pi-minus'"
                :style="{ opacity: data.mfa_enabled ? 1 : 0.35 }"
                v-tooltip.bottom="
                  data.mfa_enabled
                    ? 'Two-factor authentication is on'
                    : data.mfa_enrollment_required
                      ? 'Required for this role, not set up yet'
                      : 'Not set up'
                "
              />
              <span v-if="data.mfa_enrollment_required" style="font-size: smaller; opacity: 0.7"
                >pending</span
              >
              <!-- Hidden rather than disabled when there is nothing to clear,
                   on the caller's own row (clearing your own factor would be a
                   bypass, not a recovery), and on rows the caller's role cannot
                   act on - so the button never offers a click the server 403s. -->
              <Button
                v-if="canResetMfa(data)"
                v-tooltip.bottom="'Reset two-factor (they set it up again)'"
                icon="pi pi-replay"
                severity="secondary"
                text
                @click="() => user.resetMfa(data)"
              />
            </div>
          </template>
        </Column>
        <Column header="Role" field="role_name">
          <template #body="{ data }">
            <Select
              v-if="editing(data)"
              v-model:modelValue="edited.role_id"
              :options="selectableRoles"
              optionLabel="label"
              optionValue="value"
              key="value"
              :disabled="data.id == app.auth.user.id"
            />
            <span v-else>{{ data.pretty_role_name }}</span>
          </template>
        </Column>
        <Column
          header="Actions"
          headerStyle="display: flex; justify-content: flex-end; padding-right: 2rem"
        >
          <template #body="{ data }">
            <div class="row" style="justify-content: flex-end; min-width: 50px">
              <template v-if="editing(data)">
                <Button
                  v-tooltip.bottom="'Cancel'"
                  icon="pi pi-times"
                  severity="secondary"
                  text
                  @click="user.cancel"
                />
                <Button
                  v-tooltip.bottom="'Save'"
                  icon="pi pi-check"
                  severity="secondary"
                  text
                  @click="user.save"
                />
              </template>
              <template v-else>
                <Button
                  v-tooltip.bottom="!isMe(data) ? 'Remove user' : 'You cannot remove yourself'"
                  icon="pi pi-user-minus"
                  severity="secondary"
                  text
                  @click="user.delete(data)"
                  :disabled="!canModify(data.role_id) || isMe(data)"
                />
                <Button
                  v-tooltip.bottom="
                    !isMe(data)
                      ? 'Edit user'
                      : 'To edit your username and password, use the user sidebar'
                  "
                  icon="pi pi-user-edit"
                  severity="secondary"
                  text
                  @click="user.edit(data)"
                  :disabled="!canModify(data.role_id) || isMe(data)"
                />
              </template>
            </div>
          </template>
        </Column>
      </DataTable>
      <template v-if="created">
        <menu
          class="row"
          style="justify-content: space-between; margin-top: 2rem; padding: 0 0.5rem"
        >
          <span class="pi pi-user-plus" />
          <FloatLabel>
            <InputText
              id="created-username"
              v-model="created.username"
              :invalid="created.username && invalidCreated.username"
            />
            <label for="created-username">Username</label>
          </FloatLabel>
          <FloatLabel>
            <InputText
              id="created-email"
              v-model="created.email"
              :invalid="created.email && invalidCreated.email"
            />
            <label for="created-email">Email</label>
          </FloatLabel>
          <Select
            v-model:modelValue="created.role_id"
            :options="selectableRoles"
            optionLabel="label"
            optionValue="value"
            key="value"
          />
          <menu class="row" style="padding: 0 0.5rem">
            <Button
              icon="pi pi-times"
              text
              v-tooltip.bottom="'Cancel'"
              @click="reset"
              severity="secondary"
            />
            <Button
              icon="pi pi-save"
              text
              v-tooltip.bottom="'Create user'"
              @click="user.save"
              severity="secondary"
              :disabled="invalidCreated.form"
            />
          </menu>
        </menu>
        <Message icon="pi pi-info-circle" severity="secondary" style="margin-top: 0.5rem">
          A temporary password is generated when the account is created, and shown once. The new
          user must choose their own at first sign in.
        </Message>
      </template>

      <!-- The generated password, shown once. Kept in its own block rather than
           a toast: it has to stay on screen long enough to be copied. -->
      <template v-if="createdPassword">
        <div class="col" style="gap: 0.25rem; align-items: flex-start; margin-top: 2rem">
          <span style="font-size: smaller; opacity: 0.7">Temporary password:</span>
          <BaseCopyableField :field="createdPassword" @copy="reset" />
          <span class="temporary-password-note">
            Share it with the new user - it is shown only once, and they must set their own password
            at first sign in.
          </span>
        </div>
      </template>
    </section>
    <Message
      v-if="mfaPolicy"
      :icon="mfaPolicy.policy_min_role ? 'pi pi-shield' : 'pi pi-info-circle'"
      severity="secondary"
      style="margin-top: 1.5rem"
    >
      <template v-if="mfaPolicy.policy_min_role">
        Two-factor authentication is required for
        <strong>{{ mfaPolicy.policy_min_role }}</strong> accounts and above. Those accounts are held
        at a setup screen until they enable it.
      </template>
      <template v-else-if="mfaPolicy.available">
        Two-factor authentication is optional - anyone can enable it for their own account. To
        require it, set <code>mfa_required_min_role</code> in the server configuration.
      </template>
      <template v-else>
        Two-factor authentication is not configured on this server, so no account can enable it.
      </template>
    </Message>
    <menu style="justify-content: space-between; margin-top: 3rem">
      <Button icon="pi pi-user-plus" label="Add user" @click="user.create" :disabled="created" />
      <!-- Fully labelled and kept away from the per-row icon buttons: it acts
           on every account, including this one. -->
      <Button
        v-if="app.auth.user.role_name == 'owner'"
        icon="pi pi-key"
        label="Require password change for all users"
        severity="danger"
        text
        @click="user.requirePasswordChange"
      />
      <Button icon="pi pi-times" label="Close" @click="close" severity="secondary" />
    </menu>
  </Dialog>
</template>

<style scoped>
:deep(input) {
  max-width: 160px;
}

/* The surrounding column is width: min-content, so the note needs an explicit
   width to wrap instead of stretching the table cell. */
.temporary-password-note {
  max-width: 220px;
  white-space: normal;
  text-align: left;
  font-size: smaller;
  opacity: 0.6;
  margin-top: 0.35rem;
}

:deep(.field) > * {
  font-family: monospace;
  font-size: small;
}
</style>
