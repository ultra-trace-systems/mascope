<script setup>
import Button from 'primevue/button'
import Message from 'primevue/message'

import { PROCESSING_STATUSES } from '@/lib/processingStatus'
import { messageSeverity } from '@/lib/utils'
import { useApp } from '@/stores'

import { useSidebarMenu } from './state.js'

const app = useApp()
const sidebarMenu = useSidebarMenu()

// Titled by the status its files ended in, as Raw files names it; a digest
// of a status this build does not know shows its kind.
const title = (item) =>
  [PROCESSING_STATUSES[item.payload?.status]?.label ?? item.kind, item.instrument]
    .filter(Boolean)
    .join(' · ')

const latest = (item) => item.payload?.files?.[0] ?? null

const when = (item) => new Date(item.updated_utc).toLocaleString()

/**
 * Open Raw files on the files a digest names: its instrument, the status the
 * files ended in, and a time window reaching back to the oldest of them. The
 * window is by acquisition time, which is what the table filters a range on.
 */
function showFiles(item) {
  const times = (item.payload?.files ?? [])
    .map(({ datetime_utc }) => new Date(datetime_utc).getTime())
    .filter((time) => !Number.isNaN(time))
  app.data.acquisition.showFiles({
    instrument: item.instrument,
    status: item.payload?.status ?? null,
    since: times.length ? new Date(Math.min(...times) - 60_000) : null
  })
  app.ui.tab.active = 'raw files'
  sidebarMenu.open = false
  app.ui.inbox.markRead([item.notification_id])
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
      :severity="messageSeverity(item.severity)"
      :closable="false"
      :class="['kept', { resolved: item.resolved_utc }]"
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

/* A resolved digest stays until it is marked read, recessive. */
.kept.resolved {
  opacity: 0.6;
}
</style>
