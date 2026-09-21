<script setup>
import Button from 'primevue/button'
import Message from 'primevue/message'

import { useApp } from '@/stores'

import { useSidebarMenu } from './state.js'

const app = useApp()
const sidebarMenu = useSidebarMenu()

// Titles for the kinds the server keeps (NotificationKind in the backend's
// api/new/notifications/config.py); an unknown kind shows its own name.
const TITLES = {
  needs_chemistry: 'Needs a chemistry',
  calibration_failed: 'Calibration failed',
  processing_failed: 'Processing failed'
}

const title = (item) =>
  [TITLES[item.kind] ?? item.kind, item.instrument].filter(Boolean).join(' · ')

const severity = (item) => ({ warning: 'warn' })[item.severity] ?? item.severity

const latest = (item) => item.payload?.files?.[0] ?? null

const when = (item) => new Date(item.updated_utc).toLocaleString()

/**
 * Open Raw files on the files a digest names: its instrument, the status the
 * files ended in, and a time window reaching back to the oldest of them. The
 * window is by acquisition time, which is what the table filters on.
 */
function showFiles(item) {
  const status = item.payload?.status
  const times = (item.payload?.files ?? [])
    .map(({ datetime_utc }) => new Date(datetime_utc).getTime())
    .filter((time) => !Number.isNaN(time))
  app.data.instrument.focus({ instrument: item.instrument })
  app.data.acquisition.processingStatus = status ? [status] : null
  if (times.length) {
    app.data.acquisition.time.range.min = new Date(Math.min(...times) - 60_000)
    app.data.acquisition.time.range.max = null
  }
  app.ui.tab.active = 'raw files'
  sidebarMenu.open = false
  if (!item.read_utc) {
    app.ui.inbox.markRead([item.notification_id])
  }
}
</script>

<template>
  <section v-if="app.ui.inbox.items.length" class="inbox" aria-label="Kept notifications">
    <div class="row" style="align-items: center; justify-content: space-between">
      <h3>Needs attention</h3>
      <Button
        label="Mark all read"
        severity="secondary"
        text
        size="small"
        :disabled="app.ui.inbox.unread.length === 0"
        @click="app.ui.inbox.markAllRead()"
      />
    </div>
    <Message
      v-for="item in app.ui.inbox.sorted"
      :key="item.notification_id"
      :severity="severity(item)"
      :closable="false"
      :class="['kept', { read: item.read_utc, resolved: item.resolved_utc }]"
      :data-notification-id="item.notification_id"
    >
      <div class="col" style="gap: 0.35rem; width: 250px">
        <h4 style="margin: 0">{{ title(item) }}</h4>
        <p style="margin: 0">{{ item.message }}</p>
        <p v-if="latest(item)" class="latest">
          Latest: {{ latest(item).filename }}
          <template v-if="latest(item).detail">
            <br />
            {{ latest(item).detail }}
          </template>
        </p>
        <small class="meta">
          {{ when(item) }}
          <template v-if="item.resolved_utc"> · resolved</template>
        </small>
        <div class="row" style="gap: 0.25rem">
          <Button
            v-if="item.instrument"
            label="Show files"
            size="small"
            text
            @click="showFiles(item)"
          />
          <Button
            v-if="!item.read_utc"
            label="Mark read"
            severity="secondary"
            size="small"
            text
            @click="app.ui.inbox.markRead([item.notification_id])"
          />
        </div>
      </div>
    </Message>
  </section>
</template>

<style scoped>
.inbox {
  margin-bottom: 1rem;
}

.latest {
  margin: 0;
  font-size: 12px;
  opacity: 0.8;
  overflow-wrap: anywhere;
}

.meta {
  opacity: 0.6;
}

/* A read or resolved digest stays for reference, recessive. */
.kept.read,
.kept.resolved {
  opacity: 0.6;
}
</style>
