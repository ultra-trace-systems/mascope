<script setup>
import BlockUI from 'primevue/blockui'
import ConfirmDialog from 'primevue/confirmdialog'
import Message from 'primevue/message'
import Panel from 'primevue/panel'
import ProgressSpinner from 'primevue/progressspinner'
import Toast from 'primevue/toast'
import { useToast } from 'primevue/usetoast'

import { api } from '@/api'
import { runtime } from '@/lib/runtime.js'
import { beautifySnakeCase } from '@/lib/utils'
import { BaseBrandLogo } from '@/lib/base'
import BaseUpdateBanner from '@/lib/base/BaseUpdateBanner.vue'
import { useApp } from '@/stores'
import { useLocation } from '@/lib/location'
import { useUpdate } from '@/lib/update'
import { createToaster } from '@/lib/toaster'
import {
  PaneLogin,
  PaneOwnerSignup,
  PanePasswordChangeRequired,
  PaneMfaEnrollmentRequired
} from '@/lib/panes'

const { connected } = api

const app = useApp()
const toaster = createToaster(useToast())

// Instantiate the location store so it registers its shared-link import hook,
// which runs once the user authenticates.
useLocation()

// Watch for a newer frontend build shipped while the tab is open.
useUpdate().start()

// toaster; lives as long as the app
app.ui.notification.on('*', (notification) => {
  if (notification === null) return
  const { status, type, message, data, error } = notification
  if (status !== 'pending') {
    const severity =
      {
        warning: 'warn'
      }[status] ?? status

    const duration = status === 'error' ? 10000 : status === 'warning' ? 7000 : 3000

    toaster.show({
      severity,
      summary: `${beautifySnakeCase(type)} ${status}`,
      detail: message,
      life: duration
    })

    const download = data?.download ?? error?.detail?.data?.download

    if ((status === 'success' || status === 'warning') && download) {
      const link = document.createElement('a')
      link.download = download
      link.href = `${runtime.api_path}/api/temp/${link.download}`
      link.click()
    }
  }
})
</script>

<template>
  <!-- Mandatory password change - authenticated, but held out of the app.
       Must come before the authenticated branch, being a refinement of it, and
       cannot be folded into the anonymous branch below: that one waits on
       requiresOwner, which is only ever resolved for an anonymous visitor. -->
  <div v-if="app.auth.mustChangePassword" class="center" style="min-height: 80vh">
    <Panel style="width: 500px">
      <BaseBrandLogo />
      <div style="margin-top: 2rem" />
      <PanePasswordChangeRequired />
    </Panel>
  </div>
  <!-- Mandatory enrolment - authenticated, past the password gate, but the
       deployment requires a second factor this account does not have yet.
       Ordered after the password branch, matching the server: an account owing
       both replaces its password first. -->
  <div v-else-if="app.auth.mustEnrollMfa" class="center" style="min-height: 80vh">
    <Panel style="width: 500px">
      <BaseBrandLogo />
      <div style="margin-top: 2rem" />
      <PaneMfaEnrollmentRequired />
    </Panel>
  </div>
  <!-- App Routes - Authenticated user -->
  <RouterView v-else-if="app.auth.user" />
  <!-- Login / Owner Setup Screen - No authenticated user  -->
  <div
    v-else-if="app.auth.user == false && app.auth.requiresOwner !== null"
    class="center"
    style="min-height: 80vh"
  >
    <Panel style="width: 500px">
      <BaseBrandLogo />
      <div style="margin-top: 2rem" />
      <PaneOwnerSignup v-if="app.auth.requiresOwner" />
      <PaneLogin v-else />
    </Panel>
  </div>

  <!-- Loading - Checking authentication and registration status -->
  <div v-else class="col" style="min-height: 80vh; justify-content: center">
    <BaseBrandLogo />
    <ProgressSpinner />
    <strong>Identifying user...</strong>
  </div>
  <Toast
    position="bottom-right"
    v-if="!app.ui.notification.drawer"
    @close="toaster.released"
    @life-end="toaster.released"
  />
  <ConfirmDialog />
  <BaseUpdateBanner />

  <!-- Disconnected - Block UI with an overlay and show an error message -->
  <BlockUI :blocked="!connected" fullScreen />
  <div v-if="!connected" class="connection-message">
    <Message size="large" severity="error" icon="pi pi-times-circle"
      >No connection to the server</Message
    >
  </div>
</template>

<style>
@import './style.css';

#loading {
  width: 100vw;
  height: 100vh;
  display: grid;
  place-items: center;
}
.col {
  gap: 5rem;
}
strong {
  opacity: 0.5;
}
.connection-message {
  position: fixed;
  top: 2rem;
  left: 50%;
  transform: translateX(-50%);
  z-index: 9999;
}
</style>
