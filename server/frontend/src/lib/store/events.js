import { api } from '@/api'

/**
 * Manages socket record events with deduplication and targeted updates.
 *
 * @param {string} name - Store name
 * @param {string} key - Primary key field
 * @param {Object} refs - Reactive references { records, selection, detailed }
 * @param {Object} methods - Methods { sync, reloadRecord }
 * @param {Array<string>} events - Cross-store reload events (e.g., ['match_reload'])
 * @param {Object} logger - Logger instance
 * @returns {Function} cleanup - Unregister socket listeners
 */
export const useEvents = (name, key, refs, methods, events, logger, deps = null) => {
  const { records, selection, detailed } = refs
  const { sync, reloadRecord } = methods

  // Event deduplication cache with TTL
  const processedEvents = new Map()
  // TODO_configuration
  const EVENT_CACHE_TTL = 30000 // 30 seconds

  // Cleanup expired events periodically
  const cleanupInterval = setInterval(() => {
    const now = Date.now()
    for (const [eventId, timestamp] of processedEvents.entries()) {
      if (now - timestamp > EVENT_CACHE_TTL) {
        processedEvents.delete(eventId)
      }
    }
  }, 60000)

  /**
   * Handle targeted record events (created/updated/deleted/reload).
   */
  const handleRecordEvent = ({
    event_id,
    timestamp,
    operation,
    record_id,
    record,
    changed_fields
  }) => {
    // Deduplication check
    if (processedEvents.has(event_id)) {
      logger.debug(`ignoring duplicate event ${event_id}`)
      return
    }
    processedEvents.set(event_id, Date.now())

    // Type-safe comparison - int IDs (user/role) and varchar IDs (other tables)
    const index = records.value.findIndex((r) => String(r[key]) === String(record_id))

    switch (operation) {
      case 'created':
        handleCreated(record_id, record, index)
        break
      case 'updated':
        handleUpdated(record_id, record, index, timestamp, changed_fields)
        break
      case 'deleted':
        handleDeleted(record_id, index)
        break
      case 'reload':
        handleReload()
        break
    }
  }

  const handleCreated = (record_id, record, index) => {
    if (index === -1) {
      // Skip records that don't belong to the current scope (e.g. different workspace)
      if (deps) {
        const currentDeps = deps()
        const outOfScope = Object.entries(currentDeps).some(
          ([field, value]) =>
            value != null && record[field] != null && String(record[field]) !== String(value)
        )
        if (outOfScope) {
          logger.debug(`ignoring created ${record_id} (out of scope)`)
          return
        }
      }

      // Not found → add new record
      records.value = [...records.value, record]

      // Reindex all records
      records.value.forEach((r, idx) => (r.index = (idx + 1).toString()))
      logger.log(`added ${record_id}`)
    }
  }

  const handleUpdated = (record_id, record, index, timestamp, changed_fields) => {
    if (index === -1) {
      // Not found → ignore
      logger.debug(`ignoring update for unlisted record ${record_id}`)
      return
    }

    // Timestamp-based race condition check
    const existing = records.value[index]
    if (existing.updated_at && timestamp) {
      const existingTime = new Date(existing.updated_at).getTime()
      const incomingTime = new Date(timestamp).getTime()
      if (existingTime > incomingTime) {
        logger.warn(`ignoring stale update for ${record_id}`)
        return
      }
    }

    // Full replace or partial merge based on changed_fields
    let updatedRecord
    if (changed_fields && changed_fields.length > 0) {
      // Partial update - merge only changed fields
      updatedRecord = { ...existing, ...record }
      logger.debug(`partial update: ${changed_fields.join(', ')}`)
    } else {
      // Full update - replace entire record
      updatedRecord = record
    }

    // Preserve index
    updatedRecord.index = existing.index

    // Immutable update
    records.value = [
      ...records.value.slice(0, index),
      updatedRecord,
      ...records.value.slice(index + 1)
    ]

    logger.log(`updated ${record_id}`)

    // Update focused/detailed
    if (selection?.focused?.value && String(selection.focused.value[key]) === String(record_id)) {
      if (selection.singleselect) selection.focused.value = updatedRecord
      if (detailed?.value && String(detailed.value[key]) === String(record_id)) {
        detailed.value = updatedRecord
      }
    }
  }

  const handleDeleted = (record_id, index) => {
    if (index !== -1) {
      // Found → delete, type-safe immutable delete for proper reactivity with shallowRef
      records.value = records.value.filter((r) => String(r[key]) !== String(record_id))

      // Reindex remaining records
      records.value.forEach((r, idx) => (r.index = (idx + 1).toString()))
      logger.log(`removed ${record_id}`)

      // Handle focused record deletion
      if (selection?.focused?.value && String(selection.focused.value[key]) === String(record_id)) {
        selection.unfocus()
        if (detailed) detailed.value = null

        // Auto-refocus on next record
        if (selection?.prepRefocus) {
          const refocus = selection.prepRefocus()
          refocus()
        }
      }
    }
  }

  const handleReload = async (event) => {
    await sync({ context: 'socket event', event })
    await reloadRecord()
  }

  //  --- EVENT REGISTRATION ---

  // Auto-register store-specific events (CRUD + reload)
  const operations = ['created', 'updated', 'deleted', 'reload']
  operations.forEach((operation) => {
    api.socket.on(`${name}_${operation}`, handleRecordEvent)
  })

  // Register cross-store reload events (e.g., match_reload for sample store).
  // Each listener is kept so cleanup can remove exactly its own: an event name
  // may be another store's auto-registered reload channel (one store's
  // `<name>_reload` is a legitimate cross-store event for another), and an
  // unqualified off() would drop that store's listener too.
  const crossStoreHandlers = new Map()
  events.forEach((event) => {
    // Safety: prevent duplicate registration of store's own reload
    if (event === `${name}_reload`) {
      logger.warn(
        `❌ Duplicate event registration: '${event}' is auto-registered for store '${name}'. Remove from events array!`
      )
      return
    }
    const handler = async () => {
      await handleReload(event)
    }
    crossStoreHandlers.set(event, handler)
    api.socket.on(event, handler)
  })

  // Cleanup function
  const cleanup = () => {
    clearInterval(cleanupInterval)

    // Cleanup store-specific events
    operations.forEach((operation) => {
      api.socket.off(`${name}_${operation}`, handleRecordEvent)
    })

    // Cleanup cross-store events
    crossStoreHandlers.forEach((handler, event) => {
      api.socket.off(event, handler)
    })
  }

  return { cleanup }
}
