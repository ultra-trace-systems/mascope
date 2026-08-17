/**
 * Bounded presentation of notification toasts.
 *
 * PrimeVue renders every message it is handed and dismisses each one on its own
 * `life` timer, so the number of toasts on screen is the arrival rate times the
 * dismissal delay. Neither term is bounded: a batch operation reports hundreds
 * of items at once, and a background tab throttles the dismissal timers while
 * socket delivery continues at full rate. This caps what reaches the screen and
 * folds the rest into a single summary. Nothing is lost -- every notification is
 * still recorded in the notification log and counted by the sidebar badge.
 */

import { reactive } from 'vue'

/** Toasts on screen at once, excluding the overflow summary. */
export const MAX_TOASTS = 5

/** Lifetime of the overflow summary; refreshed while notifications keep arriving. */
export const SUMMARY_LIFE = 10000

/** Grace added to a toast's own lifetime before its slot is reclaimed. */
const RELEASE_GRACE = 1000

/** Severity ordering, so the summary carries the weight of its worst entry. */
const SEVERITY_RANK = { info: 0, success: 1, warn: 2, error: 3 }

const rank = (severity) => SEVERITY_RANK[severity] ?? -1

/**
 * Creates a toaster that caps concurrent toasts.
 *
 * The caller owns the PrimeVue toast service and must forward the Toast
 * component's `close` and `life-end` events to `released`; those are the only
 * signal that a toast left the screen, and without them the cap would fill up
 * and stay full.
 *
 * @param {Object} toast - PrimeVue toast service, as returned by `useToast()`
 * @returns {{show: Function, released: Function}} - `show` presents a message,
 *   `released` reclaims the slot of a toast that has gone
 */
export function createToaster(toast) {
  const live = new Set()
  let suppressed = 0
  let suppressedSeverity = null
  let summary = null
  let sequence = 0

  // Ids are assigned here rather than left to PrimeVue, which only stamps one on
  // when a Toast container is mounted to receive the message.
  const nextId = () => `notification-${sequence++}`

  /**
   * Brings the overflow summary up to date with the suppressed count.
   *
   * The summary is a reactive message that is edited in place while it is on
   * screen. Replacing it instead would send the old message through its leave
   * animation on every suppressed notification, and a tab that is not painting
   * never finishes those animations, so the elements would accumulate exactly
   * when this cap matters most.
   */
  function summarise() {
    const text = `${suppressed} more notification${suppressed === 1 ? '' : 's'}`
    if (summary) {
      summary.summary = text
      summary.severity = suppressedSeverity
      return
    }
    summary = reactive({
      id: nextId(),
      severity: suppressedSeverity,
      summary: text,
      detail: 'Open the notification log to review them.',
      life: SUMMARY_LIFE
    })
    toast.add(summary)
  }

  /**
   * Presents a message, or folds it into the overflow summary when the screen
   * is already full.
   *
   * @param {Object} message - PrimeVue message options (severity, summary,
   *   detail, life)
   */
  function show(message) {
    if (live.size < MAX_TOASTS) {
      const id = nextId()
      live.add(id)
      toast.add({ ...message, id })
      if (message.life) {
        // A message added before the Toast container mounts is dropped by the
        // toast event bus and never reports back, so the slot is also reclaimed
        // on a timer. Both paths are idempotent.
        setTimeout(() => live.delete(id), message.life + RELEASE_GRACE)
      }
      return
    }

    suppressed += 1
    if (rank(message.severity) >= rank(suppressedSeverity)) {
      suppressedSeverity = message.severity
    }
    summarise()
  }

  /**
   * Reclaims the slot of a toast that has left the screen.
   *
   * Bind to both `close` (dismissed by the user) and `life-end` (expired) on
   * the Toast component.
   *
   * @param {Object} event - PrimeVue toast event, `{ message }`
   */
  function released({ message } = {}) {
    const id = message?.id
    if (id === undefined) return
    if (summary && id === summary.id) {
      summary = null
      suppressed = 0
      suppressedSeverity = null
      return
    }
    live.delete(id)
  }

  return { show, released }
}
