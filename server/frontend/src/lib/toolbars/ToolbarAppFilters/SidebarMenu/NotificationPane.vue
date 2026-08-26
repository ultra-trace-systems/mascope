<script setup>
import { reactive, computed, watchEffect } from 'vue'

import Button from 'primevue/button'
import ScrollPanel from 'primevue/scrollpanel'
import Message from 'primevue/message'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'

import { useApp } from '@/stores'
import { beautifySnakeCase } from '@/lib/utils'

import { useSidebarMenu } from './state.js'

const app = useApp()
const sidebarMenu = useSidebarMenu()
const open = computed(() => sidebarMenu.open && sidebarMenu.tab === 'notifications')

const log = reactive({
  query: ''
})

function parseTimestamp(timestamp) {
  const [date, fulltime] = timestamp.toISOString().replace('Z', ' ').slice(0, -1).split('T')
  const [time, ms] = fulltime.split('.')
  return { date, time, ms }
}

const layer = 'sidebar_notifications_tab'
watchEffect(() => {
  if (open.value) {
    app.ui.help.set(layer)
  }
})

const vHelpLayer = app.ui.help.directive(layer)
</script>

<template>
  <div
    v-help-layer.right="
      `
      <b>Notifications</b>
      <p>
        Notifications are shown as toasts in the bottom right corner in real time.
        Here you can view a log of past notifications.
      </p>
      <p>Clearing empties this list and the unread badge; nothing is deleted on the server.</p>
      `
    "
    style="min-height: calc(100vh - 300px)"
  >
    <div class="row" style="align-items: center">
      <h2>Notifications</h2>
      <Button
        icon="pi pi-trash"
        severity="secondary"
        text
        rounded
        aria-label="Clear notifications"
        v-tooltip.bottom="'Clear notifications'"
        :disabled="app.ui.notification.log.length === 0"
        @click="app.ui.notification.clearLog()"
      />
    </div>
    <IconField style="width: 100%">
      <InputIcon>
        <i class="pi pi-search" />
      </InputIcon>
      <InputText v-model="log.query" placeholder="Search" style="width: 100%" />
    </IconField>
    <ScrollPanel>
      <Message
        v-for="{ id, type, status, message, timestamp } in app.ui.notification.log.filter(
          ({ type, status, message }) =>
            `${beautifySnakeCase(type)} ${status} ${message}`.includes(log.query)
        )"
        :key="id"
        :severity="
          {
            warning: 'warn'
          }[status] ?? status
        "
        :closable="false"
      >
        <div class="col" style="gap: 0.5rem">
          <ScrollPanel style="width: 250px">
            <h4 style="margin: 0.5rem 0">{{ beautifySnakeCase(type) }} {{ status }}</h4>
            <p style="margin: 0">
              {{ message }}
            </p>
          </ScrollPanel>
          <div
            class="row timestamp"
            style="width: 250px; opacity: 0.6; justify-content: flex-end; gap: 0"
            :set="{ date, time, ms } = parseTimestamp(timestamp)"
          >
            <span>
              {{ date }}
            </span>
            <span style="margin-left: 1rem">{{ time }}</span
            ><span>.{{ ms }}</span>
          </div>
        </div>
      </Message>
    </ScrollPanel>
  </div>
</template>

<style scoped>
.timestamp {
  margin: 0;
}

:deep(.p-scrollpanel-content) {
  padding-bottom: 0.8rem;
}

:deep(.p-message) {
  margin: 1rem 0;
}
</style>
