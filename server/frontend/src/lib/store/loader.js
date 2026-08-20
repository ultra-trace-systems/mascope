/**
 * Data loading and synchronization logic.
 *
 * @param {string} name - Store name
 * @param {string} key - Primary key field
 * @param {Function} method - Data loading function
 * @param {Object} refs - Reactive references { records, pending, selection, detailed }
 * @param {Object} config - Configuration { deps, read }
 * @param {Object} logger - Logger instance
 * @returns {Object} { sync, reloadRecord, load }
 */
export const useLoader = (name, key, method, refs, config, logger) => {
  const { records, pending, selection, detailed } = refs
  const { deps, read } = config

  /**
   * Synchronizes store data by fetching from API and updating reactive state.
   * Populates the store's .list with records and manages focus/selection state.
   *
   * @param {Object} trigger - Information about what triggered this sync (context, event)
   */
  const sync = async (trigger) => {
    // previous state setup
    const refocus = selection?.prepRefocus() ?? (() => {})
    const oldCount = records.value.length
    const context = trigger?.context ?? 'unknown'

    logger.debug(`sync triggered by ${trigger?.event ? `${context} (${trigger.event})` : context}`)
    pending.value = true

    // Resolve dependencies
    const args = deps ? deps() : undefined

    if (deps && args && context === 'initialization') {
      const allDeps = Object.keys(args)
        .map((k) => k.replace(/_(id|ids|filter)s?$/, ''))
        .join(', ')
      logger.debug(`dependencies: ${allDeps}`)
    }

    const unmetDeps = args
      ? Object.entries(args)
          .filter(([, value]) => value === null)
          .map(([k]) => k.replace(/_(id|ids|filter)s?$/, ''))
      : []

    const hasUnmetDeps = unmetDeps.length > 0

    // Load data. `pending` is cleared in `finally` because a rejected fetch
    // would otherwise latch it: the pane keeps rendering its spinner for the
    // rest of the session, with only the interceptor's toast to explain it.
    // Records are left as they were on failure - the previous rows are stale
    // but truthful, where an empty list would read as "this sample has none".
    try {
      if (hasUnmetDeps) {
        records.value = []
        logger.debug(`waiting for ${unmetDeps.join(', ')} dependency change`)
      } else {
        // Load data from API
        records.value = (await method(args)) || []
        // Add index field to all records
        records.value.forEach((record, idx) => (record.index = (idx + 1).toString()))
      }

      // Status logging
      logSyncStatus(oldCount, records.value.length, context, hasUnmetDeps, logger)

      // state management
      refocus()
    } finally {
      pending.value = false
    }
  }

  /**
   * Reload single focused record.
   */
  const reloadRecord = async () => {
    if (!selection?.focused?.value || !read) return

    // Capture the ID before async operation to prevent race conditions
    const recordId = selection.focused.value[key]

    try {
      logger.debug(`reload focused record ${recordId}`)
      const freshRecord = await read(recordId)

      // Guard: Check if selection still focused on same record after async operation
      if (!selection?.focused?.value || selection.focused.value[key] !== recordId) {
        logger.debug(`record ${recordId} unfocused during reload - skipping`)
        return
      }

      // Guard: Check if read returned valid data
      if (!freshRecord) {
        logger.warn(`reload focused record ${recordId} returned null/undefined`)
        return
      }

      // Update the record in the list
      const index = records.value.findIndex((r) => r[key] === recordId)
      if (index >= 0) {
        records.value[index] = freshRecord
      }

      // Update focused/detailed
      if (selection.singleselect) {
        selection.focused.value = freshRecord
      }
      if (detailed) {
        detailed.value = freshRecord
      }

      return freshRecord
    } catch (error) {
      logger.warn(`failed to reload focused record ${recordId}: ${error}`)
    }
  }

  const load = (context) => sync({ context })

  return { sync, reloadRecord, load }
}

/**
 * Log sync status based on record counts.
 */
const logSyncStatus = (oldCount, newCount, context, hasUnmetDeps, logger) => {
  if (newCount === 0) {
    if (oldCount > 0) {
      logger.log('cleared') // Had data, now empty
    } else if (!hasUnmetDeps) {
      // case for no records
      logger.log(`${context === 'socket event' ? 'reloaded' : 'loaded'} (0 records)`)
    }
  } else {
    // Has records
    const status = (() => {
      switch (context) {
        case 'initialization':
        case 'dependencies':
          return 'loaded'
        case 'socket event':
          return 'reloaded'
        default:
          return oldCount === 0 ? 'loaded' : 'reloaded'
      }
    })()

    logger.log(`${status} (${newCount} records)`)
  }
}
