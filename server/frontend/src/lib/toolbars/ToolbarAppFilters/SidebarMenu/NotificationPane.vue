<script setup>
import { reactive, computed, watch, watchEffect } from 'vue'

import Button from 'primevue/button'
import ScrollPanel from 'primevue/scrollpanel'
import Message from 'primevue/message'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'

import { useApp } from '@/stores'
import { beautifySnakeCase, messageSeverity } from '@/lib/utils'

import { useSidebarMenu } from './state.js'
import NotificationInbox from './NotificationInbox.vue'

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

// Read again whenever the pane opens: what was kept while this tab was away
// or its sign-in load failed shows up then.
watch(open, (isOpen) => {
  if (isOpen) app.ui.inbox.load()
})
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
      <p>
        Under <b>Needs attention</b> are the files of your instruments that failed, need a
        chemistry or could not be calibrated. They are kept until you mark them read, even
        when you were not signed in, and <b>Show files</b> opens them in Raw files.
      </p>
      <p>
        Clearing empties the log below and its count on the badge; the files under
        <b>Needs attention</b> stay counted until you mark them read or they are resolved.
        Nothing is deleted on the server.
      </p>
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
    <NotificationInbox />
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
        :severity="messageSeverity(status)"
        :closable="false"
      >
        <div class="col" style="gap: 0.5rem">
          <ScrollPanel style="width: 250px">
            <h4 style="margin: 0.5rem 0">{{ beautifySnakeCase(type) }} {{ status }}</h4>
            <!-- A batch operation composes its message as one line per item
                 (the samples it could not calibrate, the batches it could not
                 rematch). `pre-line` is what keeps those breaks: without it the
                 entries render as one run-on paragraph with nothing between
                 them, since the reasons carry no trailing punctuation. It still
                 wraps on width, so a line longer than the pane reflows, and a
                 one-line message is unchanged. `overflow-wrap: anywhere` breaks
                 a single word wider than the pane - a link to copy by hand -
                 instead of letting it run off the side. The toast surface does
                 both already, from PrimeVue's own `.p-toast` stylesheet
                 (`pre-line` and `word-break: break-word`). -->
            <p style="margin: 0; white-space: pre-line; overflow-wrap: anywhere">
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
