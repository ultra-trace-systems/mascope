import { watch } from 'vue'
import { defineStore } from 'pinia'

import { makeLogger } from '@/lib/logging'
import { useApp } from '@/stores'
import { useAuth } from '@/stores/auth'

import { LOCATION_LEVELS, normalizeLocation, levelIds } from './schema'
import { hasLocationQuery, locationFromQuery, locationToUrl } from './url'

export * from './schema'
export * from './url'

const logger = makeLogger({ prefix: 'location', icon: '🧭' })

// How long to wait for a restored chain to converge before giving up on the
// deferred visualization restore.
const CONVERGE_TIMEOUT_MS = 30000

// Poll interval and overall budget for confirming a shared location resolved.
// The chain converges asynchronously (one API round trip per level) and can
// take well over a few seconds on a cold backend, so we poll until it settles
// rather than snapshotting once - otherwise a slow-but-successful restore looks
// like a failure. A genuinely inaccessible level is detected sooner (see below);
// reaching the budget just stops the poll, it does not warn.
const VERIFY_POLL_MS = 700
const VERIFY_TIMEOUT_MS = 20000

// How long to keep re-asserting a restored tab while the chain converges, so a
// late data-driven tab switch (samples loading forces 'batch') cannot win.
const TAB_SETTLE_TIMEOUT_MS = 20000

// Friendly singular labels for the warning message, keyed by location field.
const LEVEL_LABELS = {
  workspace: 'workspace',
  dataset: 'dataset',
  batch: 'batch',
  samples: 'sample',
  peak: 'peak',
  collection: 'collection',
  ions: 'ion'
}

// Resolve a dotted store path (e.g. 'match.collection') against app.data.
const resolveStore = (data, path) => path.split('.').reduce((node, part) => node?.[part], data)

// Ids currently focused/selected at a chain level, as strings. Ids from a URL
// are strings while store ids may be numeric (e.g. peak_id), so coerce to keep
// comparisons type-safe.
const currentLevelIds = (data, level) => {
  const store = resolveStore(data, level.path)
  if (!store) return []
  const ids = level.multi ? store.selectedIds : store.focusedId != null ? [store.focusedId] : []
  return ids.map(String)
}

// The shallowest requested level whose target ids are not all resolved yet.
const firstUnresolved = (data, requested) =>
  LOCATION_LEVELS.find((level) => {
    const want = levelIds(requested, level)
    if (want.length === 0) return false
    const have = currentLevelIds(data, level)
    return want.some((id) => !have.includes(String(id)))
  })

/**
 * Drive a single chain level toward its target ids. Levels whose target
 * records are already resident are applied immediately; otherwise the target is
 * queued (lazyFocus / lazySelect) so it applies as the data streams in behind
 * the parent's focus.
 */
const applyLevel = (store, level, loc) => {
  const ids = levelIds(loc, level)
  const { key, multi } = level

  if (ids.length === 0) {
    store.unfocus?.()
    return
  }

  const present = store.list.filter((record) => ids.includes(record[key]))
  const allPresent = present.length === ids.length

  if (multi) {
    if (allPresent) {
      store.unfocus?.()
      store.select?.(...ids.map((id) => ({ [key]: id })))
    } else {
      store.lazySelect?.(ids)
    }
  } else if (allPresent) {
    store.focus?.({ [key]: ids[0] })
  } else {
    store.lazyFocus?.({ [key]: ids[0] })
  }
}

/**
 * The match visualization is imperative state, not a data store, so it is
 * restored last: once the sample, collection and ion it needs have loaded, fire
 * a single set(). It is driven by the explicitly visualized ion, never by the
 * ion table selection - selecting ions does not open the visualization. Only a
 * single-sample context is restorable (the visualization query is per sample).
 */
const restoreVisualization = (data, loc) => {
  if (!loc.collection || !loc.visualizedIon || loc.samples.length !== 1) return

  const sampleId = loc.samples[0]
  const collectionId = loc.collection
  const ionId = loc.visualizedIon
  const isotopeId = loc.isotope

  const ready = () =>
    data.sample.focusedId === sampleId &&
    data.match.collection.focusedId === collectionId &&
    data.match.ion.list.some((ion) => ion.target_ion_id === ionId)

  const restore = () => {
    logger.debug('restoring visualization', { data: { sampleId, collectionId, ionId } })
    data.match.visualized.set({ sampleId, collectionId, ionId, isotopeId })
  }

  // Already resident and focused (in-session apply): restore now.
  if (ready()) {
    restore()
    return
  }

  // Otherwise wait for the chain to converge, then fire once.
  const stop = watch(ready, (isReady) => {
    if (!isReady) return
    restore()
    stop()
  })

  // Give up watching if the context never resolves (e.g. no access to the ion).
  setTimeout(stop, CONVERGE_TIMEOUT_MS)
}

export const useLocation = defineStore('app.location', () => {
  /**
   * Derive the current Location from the live stores.
   */
  const read = () => {
    const { data, ui } = useApp()
    const raw = {}
    for (const level of LOCATION_LEVELS) {
      const store = resolveStore(data, level.path)
      if (!store) continue
      raw[level.field] = level.multi ? [...store.selectedIds] : store.focusedId
    }
    raw.visualizedIon = data.match.visualized.ion?.target_ion_id ?? null
    raw.isotope = data.match.visualized.isotopeSelected?.target_isotope_id ?? null
    raw.tab = ui.tab.active
    return normalizeLocation(raw)
  }

  /**
   * Drive the stores to converge on a target location: the chain, then the
   * visualization, then the active tab.
   */
  const apply = (input) => {
    const loc = normalizeLocation(input)
    const { data } = useApp()
    logger.debug('applying location', { data: { loc } })

    for (const level of LOCATION_LEVELS) {
      const store = resolveStore(data, level.path)
      if (store) applyLevel(store, level, loc)
    }

    restoreVisualization(data, loc)
    restoreTab(loc)
  }

  /**
   * Restore the active tab. 'match' follows the visualization (the tab store's
   * own guard drives it there once an ion is visualized). Other tabs are set
   * now and then re-asserted until the chain settles, so a late data-driven tab
   * switch - e.g. samples loading forces 'batch' - cannot override the restored
   * tab when the chain converges slower than the initial hydrate window.
   */
  const restoreTab = (loc) => {
    const { ui } = useApp()
    if (!loc.tab || loc.tab === 'match') return

    ui.tab.hydrate(loc.tab)

    const deadline = Date.now() + TAB_SETTLE_TIMEOUT_MS
    const settle = () => {
      const { data } = useApp()
      if (!firstUnresolved(data, loc)) {
        ui.tab.active = loc.tab
        ui.tab.endHydrate()
        return
      }
      if (Date.now() >= deadline) {
        ui.tab.endHydrate()
        return
      }
      setTimeout(settle, VERIFY_POLL_MS)
    }
    setTimeout(settle, VERIFY_POLL_MS)
  }

  /**
   * Confirm a shared location actually resolved, and warn only if it genuinely
   * could not - typically because the viewer lacks access to a workspace/dataset
   * or the record was deleted. The chain converges asynchronously, so we poll
   * until it settles (no warning) instead of snapshotting once. A level is
   * reported as soon as it is provably inaccessible (its records have loaded yet
   * still lack the target), or, failing that, after the overall timeout; only
   * the shallowest unresolved level - the chain-break point - is named.
   */
  const verifyApplied = (requested) => {
    const deadline = Date.now() + VERIFY_TIMEOUT_MS

    const warn = (level) => {
      const label = LEVEL_LABELS[level.field] ?? level.field
      logger.warn('shared location unresolved', { data: { level: level.field } })
      useApp().ui.notification.push({
        type: 'shared_link',
        status: 'warning',
        message: `Could not open the shared ${label}; you may not have access to it.`
      })
    }

    const poll = () => {
      const { data } = useApp()
      const level = firstUnresolved(data, requested)
      if (!level) return // chain settled - nothing to warn about

      // Warn only when a level is provably inaccessible: its records have
      // finished loading yet still lack the target, so waiting longer will not
      // help. A slow-but-successful load keeps polling and never warns; reaching
      // the deadline just stops the poll quietly.
      const store = resolveStore(data, level.path)
      const want = levelIds(requested, level)
      const confirmedAbsent =
        store &&
        !store.pending &&
        store.list.length > 0 &&
        want.every((id) => !store.list.some((r) => String(r[level.key]) === String(id)))

      if (confirmedAbsent) {
        warn(level)
        return
      }
      if (Date.now() >= deadline) {
        logger.debug('shared location still converging at verify deadline', {
          data: { level: level.field }
        })
        return
      }
      setTimeout(poll, VERIFY_POLL_MS)
    }

    setTimeout(poll, VERIFY_POLL_MS)
  }

  /** Full shareable URL for the current location. */
  const shareUrl = () =>
    locationToUrl(read(), {
      origin: window.location.origin,
      pathname: window.location.pathname
    })

  /** Copy a link to the current view to the clipboard, notifying the user. */
  const copyShareLink = async () => {
    const url = shareUrl()
    const { ui } = useApp()
    try {
      await navigator.clipboard.writeText(url)
      ui.notification.push({
        type: 'shared_link',
        status: 'success',
        message: 'Link to this view copied to clipboard'
      })
    } catch (error) {
      logger.warn('clipboard write failed', { data: { error: String(error) } })
      ui.notification.push({
        type: 'shared_link',
        status: 'warning',
        message: 'Could not copy the link automatically'
      })
    }
    return url
  }

  /**
   * If the current URL carries a shared location, apply it (winning over the
   * personal snapshot) and schedule an access check, then strip the query so the
   * address bar stays clean. The applied location is written to the personal
   * snapshot as the chain focuses, so a later manual reload restores it.
   */
  const importFromUrl = () => {
    const search = window.location.search
    if (!hasLocationQuery(search)) return false

    const loc = locationFromQuery(search)
    logger.log('opening shared location from URL')
    apply(loc)
    window.history.replaceState({}, '', window.location.pathname)
    verifyApplied(loc)
    return true
  }

  // Import a shared location once the user is authenticated and the stores
  // start loading; the lazy targets set by apply() win the ensuing refocus.
  useAuth().onLogin(() => importFromUrl())

  return { read, apply, shareUrl, copyShareLink, importFromUrl }
})
