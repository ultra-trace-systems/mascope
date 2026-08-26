import { reactive, watch, computed, getCurrentScope, onScopeDispose } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api'
import { genId } from '@/lib/utils'

export const useNotification = defineStore('app.ui.notification', () => {
  const retentionLimit = 250
  const state = reactive({
    latest: null, // The most recent notification that is currently displayed to the user
    log: [], // A history of all non-pending notifications, up to the retention limit
    watchers: [], // Registered callbacks that trigger on specific notification types
    progress: [], // Active processes being tracked for ongoing notifications
    recentWarnings: 0, // Track the number of recent warnings
    recentErrors: 0 // Track the number of recent errors
  })

  // Socket listener for incoming notifications
  api.socket.on('user_notification', handleNotification)

  /**
   * Central handler for incoming notifications. Decides whether to display, log, or track a notification based on its properties.
   * @param {Object} notification - The notification object received from the server
   */
  function handleNotification(notification) {
    const id = genId()
    const newNotification = {
      id,
      timestamp: new Date(),
      ...notification
    }

    // Increments recentWarnings or recentErrors counters based on notification status.
    if (notification.status === 'warning') {
      state.recentWarnings++
    } else if (notification.status === 'error') {
      state.recentErrors++
    }

    if (notification.process_id) {
      handleProcessNotification(newNotification)
    } else {
      logNotification(newNotification)
      displayNotification(newNotification)
    }
  }

  /**
   * Handles notifications that are part of a process (identified by `process_id`).
   * - Main process notifications (with `process_id` but no `parent_id`) are shown and logged.
   * - Child notifications (with both `process_id` and `parent_id`) are logged but not shown.
   * - Notifications with a status of 'pending' are not logged but tracked for progress.
   * @param {Object} notification - The notification object to process
   */
  function handleProcessNotification(notification) {
    const root_id = findRoot(notification.process_id)
    const existingProcess = state.progress.find(
      (proc) => proc.process_id === notification.process_id
    )

    if (!existingProcess) {
      if (notification.status === 'pending') {
        trackProcess(notification, root_id)
      }
    } else {
      updateProcess(existingProcess, notification)
    }

    if (notification.status !== 'pending') {
      logNotification(notification)
    }

    if (!notification.parent_id) {
      displayNotification(notification)
    }
  }

  /**
   * Initiates tracking of a new process or sub-process and sets a timeout for automatic removal.
   * @param {Object} notification - The notification representing the process
   * @param {string} root_id - The ID of the root process
   */
  function trackProcess(notification, root_id) {
    state.progress.push({
      ...notification,
      root_id,
      timeout: setTimeout(() => {
        removeProcess(notification.process_id)
      }, 30 * 1000)
    })
  }

  /**
   * Updates an existing tracked process with new information, and extends the tracking timeout.
   * Cleans up completed or errored processes.
   * @param {Object} process - The existing process being tracked
   * @param {Object} notification - The new notification data to update the process with
   */
  function updateProcess(process, notification) {
    clearTimeout(process.timeout)
    process.timeout = setTimeout(() => {
      removeProcess(notification.process_id)
    }, 30 * 1000)
    process.progress = notification.progress
    process.message = notification.message

    if (notification.status !== 'pending') {
      removeProcess(notification.process_id)
      if (notification.status === 'error') {
        removeChildProcesses(process.root_id)
      }
    }
  }

  /**
   * Removes a process from tracking.
   * @param {string} process_id - The ID of the process to remove
   */
  function removeProcess(process_id) {
    state.progress = state.progress.filter((proc) => proc.process_id !== process_id)
  }

  /**
   * Removes all child processes associated with a root process.
   * @param {string} root_id - The ID of the root process
   */
  function removeChildProcesses(root_id) {
    state.progress = state.progress.filter((proc) => proc.root_id !== root_id)
  }

  /**
   * Projects a notification onto the fields the log renders.
   *
   * `data` and `error` carry the operation's full result -- match results,
   * calibration fits, affected-id lists -- and are unbounded in size. The log
   * holds up to the retention limit for the lifetime of the tab, so keeping
   * those payloads pins them long after the live consumers of `latest` have
   * read them. The result is a new object: the notification passed in is the
   * same one handed to `displayNotification`, which must keep the full payload.
   *
   * @param {Object} notification - The notification to project
   * @returns {Object} - The entry to retain
   */
  function toLogEntry({ id, timestamp, type, status, message, process_id, parent_id }) {
    return { id, timestamp, type, status, message, process_id, parent_id }
  }

  /**
   * Logs a notification to the log, respecting the retention limit.
   * @param {Object} notification - The notification to log
   */
  function logNotification(notification) {
    state.log.unshift(toLogEntry(notification))
    if (state.log.length > retentionLimit) {
      state.log.pop()
    }
  }

  /**
   * Displays a notification as the latest notification.
   * @param {Object} notification - The notification to display
   */
  function displayNotification(notification) {
    // Only display if it's a new notification
    if (state.latest?.id === notification.id) return
    state.latest = notification
  }

  /**
   * Finds the root process ID for a given process ID by recursively checking parent IDs.
   * @param {string} process_id - The ID of the process to find the root for
   * @returns {string} - The root process ID
   */
  function findRoot(process_id) {
    const process = state.progress.find((proc) => proc.process_id === process_id)
    if (process?.parent_id) {
      return findRoot(process.parent_id)
    }
    return process_id
  }

  /**
   * Clears the latest notification.
   *
   * This method is used to reset the latest notification in the state to `null`.
   */
  function clearLatest() {
    state.latest = null
  }

  /**
   * Empties the notification log, and the unread badge with it.
   *
   * The badge counts warnings and errors from the rows this call just
   * deleted, so it goes with them: the drawer's own reset only fires when the
   * notifications tab is opened, not while it stays open, which is exactly
   * when this button is reachable.
   *
   * Nothing beyond those two. `progress` tracks live processes whose removal
   * timeouts are still pending, and `latest` drives the registered watchers
   * -- emptying the feed is not a request to cancel either. Toasts already on
   * screen keep their own dismissal timers and go on their own.
   */
  function clearLog() {
    state.log = []
    clearRecentBadge()
  }

  /**
   * Resets the recentWarnings and recentErrors counters to zero.
   *
   * Called from two places: the watchEffect that fires when the drawer is
   * opened onto the notifications tab (the user has seen them), and
   * `clearLog`, which deletes the rows the counters were counting.
   */
  function clearRecentBadge() {
    state.recentWarnings = 0
    state.recentErrors = 0
  }

  /**
   * Registers a watcher for notifications.
   * The callback will be triggered for the specified notification type(s).
   *
   * Registering from a component or effect scope binds the watcher to it: the
   * callback is dropped when that scope is disposed, so a pane that is opened
   * and closed repeatedly leaves nothing behind. A caller outside any scope
   * owns the returned `remove` and must call it itself.
   *
   * @param {String|Array} trigger - The notification type(s) to watch for
   * @param {Function} callback - The function to call when the notification is received
   * @returns {Object} - An object containing a `remove` method to unregister the watcher
   */
  function on(trigger, callback) {
    const id = genId()
    const types = Array.isArray(trigger) ? trigger : [trigger]
    state.latest = null
    types.forEach((type) => {
      state.watchers.push({ id, type, callback })
    })

    const remove = () => {
      state.watchers = state.watchers.filter((watcher) => watcher.id !== id)
    }

    if (getCurrentScope()) {
      onScopeDispose(remove)
    }

    return { remove }
  }

  // Automatically trigger registered watchers when a new notification is received
  watch(
    () => state.latest,
    (latest) => {
      if (latest?.id) {
        state.watchers.forEach(({ type, callback }) => {
          if (type === latest.type || type === '*') {
            callback(state.latest)
          }
        })
      }
    }
  )

  return {
    on,
    push: handleNotification,
    clearLatest,
    clearLog,
    clearRecentBadge,
    latest: computed(() => state.latest),
    log: computed(() => state.log),
    progress: computed(() => state.progress),
    recentWarnings: computed(() => state.recentWarnings),
    recentErrors: computed(() => state.recentErrors)
  }
})
