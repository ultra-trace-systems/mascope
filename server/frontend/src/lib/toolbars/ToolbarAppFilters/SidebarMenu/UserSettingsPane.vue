<script setup>
import { ref, reactive, computed, watchEffect, watch } from 'vue'

import ToggleSwitch from 'primevue/toggleswitch'
import Message from 'primevue/message'
import Select from 'primevue/select'
import Button from 'primevue/button'

import { api } from '@/api'
import { getApiErrorMessage, needsMfaReauth } from '@/api/utils'
import { useApp } from '@/stores'
import { BaseCopyableField, BaseEditableField } from '@/lib/base'
import {
  DialogUserManagement,
  DialogPasswordChange,
  DialogAgentPairing,
  DialogMfaSetup,
  DialogMfaReauth
} from '@/lib/dialogs'
import { useMfaReauth } from '@/lib/dialogs/useMfaReauth'
import { prettyRoleName, ROLES } from '@/lib/roles'

import { useSidebarMenu } from './state.js'

const app = useApp()
const sidebarMenu = useSidebarMenu()

const open = computed(() => sidebarMenu.open && sidebarMenu.tab === 'settings')

const dialog = reactive({
  users: false,
  password: false,
  pairing: false,
  mfa: false
})

// The shared step-up-and-retry protocol: token regeneration below runs through
// it, so a code is asked for only when the server refuses.
const { reauthVisible, runWithReauth, onVerified } = useMfaReauth()

const onReauthVerified = onVerified((e) =>
  app.ui.notification.push({
    type: 'mfa_reauth',
    status: 'error',
    message: getApiErrorMessage(e, 'Could not complete the action.')
  })
)

// TODO_config API Token Management
const SERVICE_CONFIGS = [
  {
    id: 'mascope_sdk', // used for both internal reference in selectedTokenType and API requests for packages
    label: 'Jupyter Notebooks',
    minRole: 100 // guest role_id
  },
  {
    id: 'tof-agent', // Different delimiter styles (_ vs -) are used in id (packages/libraries use _, services/agents use -).
    label: 'TOF Agent',
    minRole: 200 // editor role_id
  },
  {
    id: 'file-agent',
    // The shipped File Agent obtains its credential by pairing and has no way
    // to accept a pasted one, so a token minted here could not be used. The
    // TOF and CSV export agents keep their entry: they are external clients
    // that cannot pair, and this is their only way to get a credential.
    pairedOnly: true,
    label: 'File Agent',
    minRole: 200 // editor role_id
  },
  {
    id: 'export-agent',
    label: 'CSV Export Agent',
    minRole: 200 // editor role_id
  }
]

// Fixed-name asset uploaded to every GitHub release by CI, so this
// URL always resolves to the newest released installer
const FILE_AGENT_DOWNLOAD_URL =
  'https://github.com/ultra-trace-systems/mascope/releases/latest/download/Mascope-File-Agent-Setup.exe'

const token = ref(null)
const selectedTokenType = ref('mascope_sdk')

const currentServiceConfig = computed(() =>
  SERVICE_CONFIGS.find((c) => c.id === selectedTokenType.value)
)

// Available token types based on user role
const availableTokenTypes = computed(() =>
  SERVICE_CONFIGS.filter(
    (config) => !config.pairedOnly && app.auth.user.role_id >= config.minRole
  )
)

const tokenItems = computed(() =>
  availableTokenTypes.value.map((config) => ({
    value: config.id,
    label: config.label
  }))
)

const regenerateToken = () => runWithReauth(_regenerateToken)

const _regenerateToken = async () => {
  const config = currentServiceConfig.value
  if (!config) return
  try {
    token.value = (
      await api.http.post(`/auth/access_token/regenerate`, {
        service_name: config.id
      })
    )?.data?.access_token
  } catch (e) {
    if (needsMfaReauth(e)) throw e
    app.ui.notification.push({
      type: `${config.id}_token_refresh`,
      status: 'error',
      message: getApiErrorMessage(e, 'Failed to regenerate the access token.')
    })
  }
}

// Paired machines: devices created by pairing approval, listed so each can
// be renamed or revoked on its own (unlike Regenerate, which replaces every
// token of a service at once). Revoked devices are history, not settings, so
// the list shows only live ones.
const devices = ref([])
const confirmRevokeId = ref(null)

const serviceLabel = (serviceName) =>
  SERVICE_CONFIGS.find((c) => c.id === serviceName)?.label ?? serviceName

const fetchDevices = async () => {
  if (app.auth.user.role_id < ROLES.editor) return
  try {
    const response = await api.http.get('/auth/devices')
    devices.value = (response?.data?.data ?? []).filter((d) => !d.revoked_at)
  } catch (e) {
    app.ui.notification.push({
      type: 'devices_fetch',
      status: 'error',
      message: getApiErrorMessage(e, 'Failed to load paired machines.')
    })
  }
}

const renameDevice = async (device, name) => {
  try {
    await api.http.patch(`/auth/devices/${device.device_id}`, { name })
    await fetchDevices()
  } catch (e) {
    app.ui.notification.push({
      type: 'device_rename',
      status: 'error',
      message: getApiErrorMessage(e, 'Failed to rename the machine.')
    })
  }
}

// First click arms the confirmation, second click revokes; selecting another
// row or closing the drawer disarms it.
const revokeDevice = async (device) => {
  if (confirmRevokeId.value !== device.device_id) {
    confirmRevokeId.value = device.device_id
    return
  }
  confirmRevokeId.value = null
  try {
    await api.http.delete(`/auth/devices/${device.device_id}`)
    await fetchDevices()
  } catch (e) {
    app.ui.notification.push({
      type: 'device_revoke',
      status: 'error',
      message: getApiErrorMessage(e, 'Failed to revoke the machine.')
    })
  }
}

const lastSeenLabel = (device) =>
  device.last_seen_at
    ? `last seen ${new Date(device.last_seen_at).toLocaleString()}`
    : 'never seen'

// Clear state when closing drawer
const clear = () => {
  token.value = null
  selectedTokenType.value = 'mascope_sdk'
  confirmRevokeId.value = null
}

// Watch drawer visibility: load the device list on open, clear state on close
watch(open, (visible) => {
  if (visible) fetchDevices()
  else clear()
})

// A pairing approved through the dialog creates a device; refresh on close
watch(
  () => dialog.pairing,
  (visible) => {
    if (!visible && open.value) fetchDevices()
  }
)

watchEffect(() => {
  if (app.ui.darkmode.active) {
    document.documentElement.classList.add('darkmode')
    localStorage.setItem('mascope-darkmode', 'true')
  } else {
    document.documentElement.classList.remove('darkmode')
    localStorage.setItem('mascope-darkmode', 'false')
  }
})

const layer = 'sidebar_settings_tab'
watchEffect(() => {
  if (open.value) {
    app.ui.help.set(layer)
  }
})

const vHelpLayer = app.ui.help.directive(layer)
</script>

<template>
  <h2>Settings</h2>
  <section
    v-help-layer.right="
      `
    <b>Account Settings</b>
    <p>View your sign-in details, user role, and change your password.</p>
  `
    "
  >
    <h3>Account</h3>
    <BaseEditableField
      :field="app.auth.user.username"
      :save="(username) => app.data.user.update({ username })"
    />
    <ul>
      <li>📧 {{ app.auth.user.email }}</li>
      <li>{{ prettyRoleName(app.auth.user) }}</li>
    </ul>
    <Button
      label="Change password"
      @click="() => (dialog.password = true)"
      severity="secondary"
      text
      icon="pi ph ph-lock-key"
    />
    <Button
      label="Two-factor authentication"
      @click="() => (dialog.mfa = true)"
      severity="secondary"
      text
      icon="pi pi-shield"
    />
  </section>
  <section
    v-help-layer.right="
      `
    <b>Theme</b>
    <p>Select between light and dark mode for the Mascope interface.</p>
  `
    "
  >
    <h3>Theme</h3>
    <div class="row" style="width: fit-content">
      <span>Light</span>
      <span class="pi pi-sun" />
      <ToggleSwitch v-model="app.ui.darkmode.active" />
      <span class="pi pi-moon" />
      <span>Dark</span>
    </div>
  </section>
  <section
    v-help-layer.right="{
      message: `
      <b>API Access Tokens</b>
      <p>
        API tokens are used for authentication when accessing Mascope programmatically,
        e.g., from Jupyter Notebooks or the instrument agents. Here you can generate
        tokens for specific services; each token is shown only once.
      </p>
      <p>
        The File Agent uploads data files from an instrument PC automatically — its
        Windows installer can be downloaded below. It is connected by pairing, not
        by copying a token: run its setup, then enter the code it shows via
        'Pair an agent'.
      </p>
      <p>
        Machines you have paired appear under 'Paired machines', where each can be
        renamed or revoked on its own. A paired machine holds its own credential,
        so Regenerate does not affect it: Regenerate replaces the tokens issued to
        you, and revoking a machine is what stops that machine.
      </p>
    `,
      doc: app.ui.help.docUrl('instruments/#the-file-agent')
    }"
  >
    <h3>API Access Tokens</h3>
    <div id="token-container">
      <div id="token-controls">
        <Select
          v-model="selectedTokenType"
          :options="tokenItems"
          optionLabel="label"
          optionValue="value"
          id="token-service-select"
          @change="token = null"
        />
        <Button
          icon="pi pi-refresh"
          label="Regenerate"
          @click="regenerateToken"
          id="token-button"
        />
      </div>
      <div v-if="token" id="token-info">
        <div id="token-display">
          <span class="pi pi-lock" style="opacity: 0.3" />
          <BaseCopyableField :field="token" />
        </div>
        <Message icon="pi pi-info-circle" severity="info" closable>
          <p>Token is shown only once for security reasons; if you lose it, regenerate a new one</p>
        </Message>
      </div>
      <Button
        v-if="app.auth.user.role_id >= ROLES.editor"
        as="a"
        :href="FILE_AGENT_DOWNLOAD_URL"
        label="Download File Agent installer"
        icon="pi pi-download"
        severity="secondary"
        text
        id="agent-download-button"
      />
      <Button
        v-if="app.auth.user.role_id >= ROLES.editor"
        label="Pair an agent"
        icon="pi pi-link"
        severity="secondary"
        text
        id="agent-pairing-button"
        @click="() => (dialog.pairing = true)"
      />
      <div v-if="devices.length" id="paired-devices">
        <h4>Paired machines</h4>
        <ul>
          <li v-for="device in devices" :key="device.device_id" class="device-row">
            <BaseEditableField
              :field="device.name"
              :save="(name) => renameDevice(device, name)"
            />
            <span class="device-meta">
              {{ serviceLabel(device.service_name) }} · {{ lastSeenLabel(device) }}
            </span>
            <Button
              :label="confirmRevokeId === device.device_id ? 'Confirm revoke' : 'Revoke'"
              icon="pi pi-ban"
              severity="danger"
              text
              size="small"
              @click="revokeDevice(device)"
            />
          </li>
        </ul>
      </div>
    </div>
  </section>
  <section
    v-if="app.auth.user.role_id >= ROLES.admin"
    v-help-layer.right="
      `
  <b>Admin Settings</b>
  <p>Add, remove and modify users</p>
  `
    "
  >
    <h3>Admin</h3>
    <Button icon="pi pi-users" @click="() => (dialog.users = true)" label="Manage users" />
  </section>
  <DialogUserManagement v-model:visible="dialog.users" />
  <DialogPasswordChange v-model:visible="dialog.password" />
  <DialogAgentPairing v-model:visible="dialog.pairing" />
  <DialogMfaSetup v-model:visible="dialog.mfa" />
  <DialogMfaReauth v-model:visible="reauthVisible" @verified="onReauthVerified" />
</template>

<style scoped>
.col {
  gap: 0rem;
}

#token-container {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  min-height: 3rem;

  #token-controls {
    display: flex;
    align-items: flex-start;
    gap: 1.5rem;
    width: 100%;

    #token-button {
      flex-shrink: 0;
    }

    #token-service-select {
      width: 100%;
    }
  }

  #agent-download-button,
  #agent-pairing-button {
    width: fit-content;
  }

  #paired-devices {
    h4 {
      margin: 0 0 0.25rem;
    }

    ul {
      margin: 0;

      .device-row {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        flex-wrap: wrap;

        .device-meta {
          font-size: smaller;
          opacity: 0.7;
        }
      }
    }
  }

  #token-info {
    display: flex;
    flex-direction: column;
    gap: 1rem;

    #token-display {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      border: 1px solid var(--p-drawer-border-color);
      padding: 0.75rem;
      border-radius: 1rem;
      font-size: smaller;
      width: 100%;
      word-break: break-all;
    }
  }
}

section:not(:first-child) {
  margin-top: 2rem;
  border-top: 1px solid var(--p-drawer-border-color);
}

ul {
  list-style: none;
  padding-left: 0.5em;

  li {
    margin: 0.7rem 0;

    i {
      margin-right: 0.2rem;
    }
  }
}
</style>
