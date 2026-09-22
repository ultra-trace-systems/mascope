import { ref, computed, watch } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { useAuth } from '@/stores/auth'

// How many kept notifications to load: the server's largest page. Only unread
// ones are loaded; a digest marked read is dismissed from the pane, and each
// file it named keeps its status in Raw files.
const LIMIT = 200

/**
 * Notifications the server keeps until they are read (`/api/notifications`).
 *
 * A live notification (`./notification.js`) reaches only a tab that is open
 * when it is sent. These are the processing outcomes that need someone -
 * files of an instrument that failed, need a chemistry or could not be
 * calibrated - kept for the people answerable for the instrument. Each is a
 * digest: one unread row per kind and instrument, which grows with every such
 * file until it is read, and is marked resolved once none of its files is
 * left in that state.
 *
 * Every change to a row raises its `version`, so of two copies - a loaded
 * page and a socket update, or two updates from different workers - the
 * newer wins whichever arrives last.
 */
export const useInbox = defineStore('app.ui.inbox', () => {
  const auth = useAuth()
  const items = ref([])

  // The most recently changed first.
  const sorted = computed(() =>
    [...items.value].sort((a, b) => String(b.updated_utc).localeCompare(String(a.updated_utc)))
  )
  const unread = computed(() => items.value.filter((item) => !item.read_utc))
  // What still asks for someone: unread, and not resolved since.
  const pending = computed(() => unread.value.filter((item) => !item.resolved_utc))
  const pendingErrors = computed(
    () => pending.value.filter((item) => item.severity === 'error').length
  )

  /**
   * The list with `record` in place of the copy it holds, unless that copy
   * is newer. A read row leaves the list.
   */
  function apply(list, record) {
    const index = list.findIndex((item) => item.notification_id === record.notification_id)
    if (index >= 0 && (record.version ?? 0) < (list[index].version ?? 0)) return list
    const rest = index >= 0 ? list.filter((_, i) => i !== index) : list
    return record.read_utc ? rest : [record, ...rest]
  }

  // Updates that arrive while a load is in flight are applied again on top of
  // its answer, which may predate them; only the latest load is kept.
  let latestLoad = 0
  let updatesDuringLoad = null
  const userId = () => (auth.user && typeof auth.user === 'object' ? auth.user.id : null)

  const upsert = (record) => {
    updatesDuringLoad?.push(record)
    items.value = apply(items.value, record)
  }

  async function load() {
    const loadId = ++latestLoad
    const forUser = userId()
    updatesDuringLoad ??= []
    let rows = null
    try {
      const response = await api.http.get('/notifications', {
        params: { limit: LIMIT },
        type: 'load_notifications'
      })
      rows = response.data?.data ?? []
    } catch {
      // Reported by the HTTP layer; opening the pane loads again.
    }
    if (loadId !== latestLoad) return
    const updates = updatesDuringLoad ?? []
    updatesDuringLoad = null
    // Another account signed in meanwhile: this answer is not theirs.
    if (rows === null || forUser !== userId()) return
    items.value = updates.reduce(
      apply,
      rows.filter((row) => !row.read_utc)
    )
  }

  const dismiss = (ids) => {
    items.value = items.value.filter((item) => !ids.includes(item.notification_id))
  }

  /**
   * Mark digests read, as they are shown. One that took another file since
   * is left unread by the server, and stays.
   *
   * @param {string[]} ids The digests to mark.
   */
  async function markRead(ids) {
    const seen = items.value
      .filter((item) => ids.includes(item.notification_id))
      .map(({ notification_id, updated_utc }) => ({ notification_id, updated_utc }))
    if (!seen.length) return
    const response = await api.http.post(
      '/notifications/read',
      { notifications: seen },
      { type: 'read_notifications' }
    )
    dismiss(response?.data?.data?.notification_ids ?? [])
  }

  const markAllRead = () => markRead(unread.value.map((item) => item.notification_id))

  // The server tells every tab of the addressee when a digest opens, grows,
  // is resolved or is read elsewhere.
  api.socket.on('notification_created', ({ record }) => upsert(record))
  api.socket.on('notification_updated', ({ record }) => upsert(record))

  // Whoever signs in next in the same tab starts from nothing: another
  // account's digests are not theirs to read, or to mark read.
  watch(userId, (id, previous) => {
    if (id !== previous) items.value = []
  })
  auth.onLogin(load)

  return {
    items,
    sorted,
    unread,
    pending,
    pendingErrors,
    load,
    markRead,
    markAllRead
  }
})
