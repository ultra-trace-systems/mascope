/**
 * Data loading and synchronization logic.
 *
 * @param {string} name - Store name
 * @param {string} key - Primary key field
 * @param {Function} method - Data loading function
 * @param {Object} refs - Reactive references { records, pending, error, selection, detailed }
 * @param {Object} config - Configuration { deps, read }
 * @param {Object} logger - Logger instance
 * @returns {Object} { sync, reloadRecord, load }
 */
export const useLoader = (name, key, method, refs, config, logger) => {
  const { records, pending, error, selection, detailed } = refs
  const { deps, read } = config

  // Syncs overlap freely - the deps watcher, socket reloads and the retry button
  // can all fire while one is in flight - and they settle in completion order,
  // not start order. Only the newest may write the store, or a slow failure
  // lands on top of a newer success and the pane shows an error over good data.
  let generation = 0

  /**
   * Synchronizes store data by fetching from API and updating reactive state.
   * Populates the store's .list with records and manages focus/selection state.
   *
   * A failed fetch is recorded on `error` and leaves the store usable - it never
   * rejects. Callers are watchers and lifecycle hooks that cannot catch, so
   * rethrowing here would only surface as an unhandled rejection.
   *
   * A caller that needs to know how ITS OWN load went must read the returned
   * outcome rather than the `error` ref: the ref is shared by every sync on the
   * store, so a concurrent one can clear it or fill it with an unrelated failure.
   *
   * @param {Object} trigger - Information about what triggered this sync (context, event)
   * @returns {Promise<{ok: boolean, error: *, superseded: boolean}>} this call's outcome
   */
  const sync = async (trigger) => {
    const seq = ++generation
    const superseded = () => seq !== generation

    // previous state setup
    const refocus = selection?.prepRefocus() ?? (() => {})
    const oldCount = records.value.length
    const context = trigger?.context ?? 'unknown'

    logger.debug(`sync triggered by ${trigger?.event ? `${context} (${trigger.event})` : context}`)
    pending.value = true
    error.value = null

    // Fetch phase. Only the load itself is caught here: a fault in the state
    // management below is a bug in this app rather than a failed request, and
    // must not reach the user as "could not load this list".
    let next = null
    let hasUnmetDeps = false
    let failure = null

    try {
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

      hasUnmetDeps = unmetDeps.length > 0

      // Load data
      if (hasUnmetDeps) {
        next = []
        logger.debug(`waiting for ${unmetDeps.join(', ')} dependency change`)
      } else {
        // Load data from API
        next = (await method(args)) || []
        // Add index field to all records
        next.forEach((record, idx) => (record.index = (idx + 1).toString()))
      }
    } catch (err) {
      failure = err
    }

    // Write phase. Only the newest sync may touch the store - an older one
    // landing here would put its rows, its failure, or a cleared `pending` on
    // top of a newer result.
    if (superseded()) {
      logger.debug(
        failure
          ? `sync superseded by a newer one - discarding its failure (${failure})`
          : 'sync superseded by a newer one - discarding its rows'
      )
      return { ok: false, error: null, superseded: true }
    }

    try {
      if (failure) {
        // Keep the rows and the selection. The pane renders the error in place
        // of the list, so nothing stale is shown - while clearing them would
        // drive refocus() against an empty list, unfocus the selection and
        // delete its persisted state, cascading into every child store. A failed
        // refresh must not cost the user their place.
        error.value = failure
        logger.warn(`sync failed (${context})`, { data: { error: failure } })
        return { ok: false, error: failure, superseded: false }
      }

      records.value = next

      // Status logging and state management, guarded apart from the fetch:
      // refocus() restores the selection and so reaches localStorage, and a
      // browser that refuses storage would otherwise turn every successful load
      // into a permanent "could not load this list" that the retry button cannot
      // clear. Logged with the error object, so the stack survives.
      try {
        logSyncStatus(oldCount, records.value.length, context, hasUnmetDeps, logger)
        refocus()
      } catch (err) {
        logger.error(`sync loaded but could not restore state (${context})`, {
          data: { error: err }
        })
      }

      return { ok: true, error: null, superseded: false }
    } finally {
      // Reached only by the newest sync; an older one clearing the flag would
      // hide a load that is still running.
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
      // Bound as `err`, not `error`: that name is the store failure ref in this
      // scope, and shadowing it invites recording a failure on the wrong one.
    } catch (err) {
      logger.warn(`failed to reload focused record ${recordId}: ${err}`)
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
