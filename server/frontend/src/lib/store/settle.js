import { nextTick } from 'vue'
import { until } from '@vueuse/core'

/**
 * How long to wait for a store to finish reloading before giving up on it.
 *
 * A backstop, not a deadline anyone should reach: the http client times out at
 * 20s, which turns a hung request into a failed sync rather than an endless
 * pending.
 */
export const STORE_SETTLE_TIMEOUT = 30_000

/**
 * Resolve once a store that is reloading has finished.
 *
 * The data stores reload on a parent switch through a plain dependency watcher,
 * so at the moment of the switch `pending` is still false and `list` still holds
 * the PREVIOUS parent's records -- reading right away would always join against
 * the wrong ones. One tick lets the reload be queued; then we wait for it to
 * finish. The early return covers a reload that never started, or one that has
 * already landed, so there is no edge left to wait for.
 *
 * The timeout RESOLVES rather than throws, so there is no rejection to handle
 * and no watcher left alive for good. That also means resolving proves nothing
 * on its own: **the caller must re-check `isPending()` afterwards** to tell a
 * real settle from the backstop firing, or it walks into a list whose contents
 * it knows nothing about. `until` races a bare timer it never cancels, so each
 * call that has to wait leaves one timer to expire on its own -- harmless, but
 * it is why testing the backstop needs fake timers.
 *
 * @param {() => boolean} isPending - reads the store's `pending` flag
 * @param {{ timeout?: number }} [options]
 * @returns {Promise<void>}
 */
export const untilStoreSettled = async (isPending, { timeout = STORE_SETTLE_TIMEOUT } = {}) => {
  await nextTick()
  if (!isPending()) return
  await until(isPending).toBe(false, { timeout })
}
