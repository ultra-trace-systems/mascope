import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { useAuth } from '@/stores/auth'

// How many kept notifications to load. The server lists unread ones first.
const LIMIT = 50

/**
 * Notifications the server keeps until they are read (`/api/notifications`).
 *
 * A live notification (`./notification.js`) reaches only a tab that is open
 * when it is sent. These are the processing outcomes that need someone -
 * files of an instrument that failed, need a chemistry or could not be
 * calibrated - kept for the people answerable for the instrument. Each is a
 * digest: one unread row per kind and instrument, which grows with every such
 * file until it is read, and is marked resolved once no file is left in that
 * state.
 */
export const useInbox = defineStore('app.ui.inbox', () => {
  const items = ref([])

  // Unread first, then the most recently changed.
  const sorted = computed(() =>
    [...items.value].sort(
      (a, b) =>
        Number(Boolean(a.read_utc)) - Number(Boolean(b.read_utc)) ||
        String(b.updated_utc).localeCompare(String(a.updated_utc))
    )
  )
  const unread = computed(() => items.value.filter((item) => !item.read_utc))
  const unreadErrors = computed(
    () => unread.value.filter((item) => item.severity === 'error').length
  )

  const upsert = (record) => {
    const index = items.value.findIndex((item) => item.notification_id === record.notification_id)
    if (index >= 0) {
      items.value[index] = record
    } else {
      items.value = [record, ...items.value]
    }
  }

  const markLocally = (ids) => {
    const now = new Date().toISOString()
    items.value = items.value.map((item) =>
      !item.read_utc && (ids === null || ids.includes(item.notification_id))
        ? { ...item, read_utc: now }
        : item
    )
  }

  async function load() {
    try {
      const response = await api.http.get('/notifications', {
        params: { include_read: true, limit: LIMIT },
        type: 'load_notifications'
      })
      items.value = response.data?.data ?? []
    } catch {
      // Reported by the HTTP layer; the live notifications still work.
    }
  }

  async function markRead(ids) {
    if (!ids?.length) return
    await api.http.post(
      '/notifications/read',
      { notification_ids: ids },
      { type: 'read_notifications' }
    )
    markLocally(ids)
  }

  async function markAllRead() {
    if (!unread.value.length) return
    await api.http.post('/notifications/read', { all: true }, { type: 'read_notifications' })
    markLocally(null)
  }

  // The server tells every tab of the addressee when a digest opens, grows,
  // is resolved or is read elsewhere.
  api.socket.on('notification_created', ({ record }) => upsert(record))
  api.socket.on('notification_updated', ({ record }) => upsert(record))

  useAuth().onLogin(load)

  return {
    items,
    sorted,
    unread,
    unreadErrors,
    load,
    markRead,
    markAllRead
  }
})
