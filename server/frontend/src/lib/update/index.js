import { ref } from 'vue'
import { defineStore } from 'pinia'

import { makeLogger } from '@/lib/logging'

/**
 * Detects that a newer frontend build has been deployed while the tab is open -
 * for example after an auto-update ships new assets - without a backend
 * endpoint. Vite emits hashed entry bundles, so the set of module-script paths
 * referenced by index.html changes on every build. We remember the set the tab
 * booted with and periodically re-fetch index.html to compare.
 *
 * This is separate from the socket reconnect reload (api/socket.js): that
 * covers updates that drop the connection; this covers updates that do not.
 */

const logger = makeLogger({ prefix: 'update', icon: '⬆️' })

// How often to re-check for a new build.
const POLL_INTERVAL_MS = 10 * 60 * 1000

/** Sorted module-script pathnames referenced by an index.html string. */
export const extractModuleScripts = (html) => {
  const scripts = new Set()
  const re = /<script\b[^>]*\btype=["']module["'][^>]*\bsrc=["']([^"']+)["']/gi
  let match
  while ((match = re.exec(html)) !== null) {
    try {
      // Normalize to a path so origin differences do not register as changes.
      scripts.add(new URL(match[1], 'http://x').pathname)
    } catch {
      scripts.add(match[1])
    }
  }
  return [...scripts].sort()
}

/** Module-script pathnames of the document currently running in the tab. */
const currentModuleScripts = () =>
  [...document.querySelectorAll('script[type="module"][src]')]
    .map((script) => {
      try {
        return new URL(script.getAttribute('src'), window.location.origin).pathname
      } catch {
        return script.getAttribute('src')
      }
    })
    .sort()

/** True when two sorted scripts lists differ. */
export const scriptsChanged = (a, b) => a.length !== b.length || a.some((s, i) => s !== b[i])

export const useUpdate = defineStore('app.update', () => {
  const available = ref(false)
  const booted = currentModuleScripts()
  let timer = null

  /** Re-fetch index.html and flag an update when the entry bundles changed. */
  const check = async () => {
    if (available.value) return true
    try {
      const response = await fetch(`${window.location.pathname}?_=${Date.now()}`, {
        cache: 'no-store',
        headers: { 'Cache-Control': 'no-cache' }
      })
      if (!response.ok) return false
      const fetched = extractModuleScripts(await response.text())
      // An empty parse means we could not read the entry bundles; ignore rather
      // than false-alarm.
      if (fetched.length > 0 && scriptsChanged(booted, fetched)) {
        logger.log('a new build is available', { data: { booted, fetched } })
        available.value = true
      }
    } catch (error) {
      logger.debug('update check failed', { data: { error: String(error) } })
    }
    return available.value
  }

  /** Begin periodic checks (also on tab refocus). Idempotent. */
  const start = () => {
    if (timer) return
    timer = setInterval(check, POLL_INTERVAL_MS)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') check()
    })
  }

  /**
   * Reload onto the new build. The tab typically got here because the browser
   * held a stale index.html as fresh, so first replace the cached copy
   * (cache: 'reload' bypasses the HTTP cache and stores the response), then
   * reload; otherwise the reload can boot the same stale build and re-flag.
   */
  const reload = async () => {
    try {
      await fetch(window.location.pathname, { cache: 'reload' })
    } catch {
      // Offline or transient failure: reload anyway.
    }
    window.location.reload()
  }

  return { available, check, start, reload }
})
