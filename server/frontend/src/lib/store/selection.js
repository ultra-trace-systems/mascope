import { ref, computed, watch } from 'vue'
import { storeToRefs } from 'pinia'

import { api } from '@/api'
import { makeLogger } from '@/lib/logging'

import { useFilter } from '@/stores/data/filter'

export const useSelection = (name, key, records, options = {}) => {
  // CONFIG

  const { mode, subscribe, persist, hook } = {
    mode: 'binary', // | 'multiple' | 'single'
    subscribe: false,
    persist: false,
    hook: null,
    ...options
  }
  // logging
  const logger = makeLogger({
    prefix: `selection ${name}`,
    icon: '🌟'
  })

  // get filter from filter store
  const filterStore = useFilter()
  const filterRefs = storeToRefs(filterStore)
  let filter = filterRefs[name]

  // if no filter exists in filter store, create local reactive ref (guard)
  if (!filter) {
    logger.warn(`Creating local selection state for '${name}' (not in filter store)`)
    filter = ref([])
  }

  const singleselect = mode === 'single' || mode === 'binary'
  const multiselect = mode === 'multiple'
  const allowUnfocus = mode !== 'single'

  // selection state
  const selected = computed({
    get() {
      return filter.value ?? []
    },
    set(value) {
      filter.value = value ?? []
    }
  })
  const selectedIds = computed(() => selected.value.map((record) => record[key]))
  const isSelected = (arg) =>
    arg ? selected.value.map((record) => record[key]).includes(arg[key]) : false

  // focus state
  const focused = computed({
    get() {
      return filter.value.length === 1 ? filter.value[0] : null
    },
    set(value) {
      filter.value = value ? [value] : []
    }
  })
  const focusedId = computed(() => (focused.value ? focused.value[key] : null))
  const isFocused = (arg) => (arg && focused.value ? focused.value[key] === arg[key] : false)

  // methods
  const select = multiselect
    ? (...args) => {
        // Assign a fresh array rather than pushing in place: the selection is
        // held in a shallowRef, so an in-place mutation would not trigger the
        // watchers (logging, persistence, subscriptions) that observe it.
        const additions = args
          .filter((arg) => !isSelected(arg))
          .map((arg) => records().find((record) => record[key] === arg[key]))
          .filter((record) => !!record)
        if (additions.length > 0) {
          selected.value = [...selected.value, ...additions]
        }
      }
    : // singleselect
      (arg) => {
        if (!isFocused(arg)) {
          focused.value = records().find((record) => record[key] === arg[key])
        }
      }
  const unselect = multiselect
    ? (...args) => {
        args.forEach((arg) => {
          if (isSelected(arg)) {
            selected.value = selected.value.filter((record) => record[key] !== arg[key])
          }
        })
      }
    : // singleselect
      (arg) => {
        if (isFocused(arg)) {
          focused.value = null
        }
      }
  const focus = multiselect
    ? (arg) => {
        if (!isFocused(arg)) {
          selected.value = [records().find((record) => record[key] === arg[key])].filter(
            (record) => !!record
          )
        }
      }
    : // singleselect
      (arg) => {
        if (typeof arg === 'function') {
          // allow predicates to focus with arbitrary conditions
          focused.value = records().find(arg)
        } else {
          // but normally, focus using a record or key field
          if (!isFocused(arg)) {
            focused.value = records().find((record) => record[key] === arg[key])
          }
        }
      }
  const unfocus = multiselect
    ? (arg) => {
        if (arg) {
          if (isSelected(arg)) {
            selected.value = []
          }
        } else {
          selected.value = []
        }
      }
    : // singleselect
      (arg) => {
        if (arg) {
          if (isFocused(arg)) {
            focused.value = null
          }
        } else {
          focused.value = null
        }
      }

  // lazy focusing
  const toFocus = ref(null)
  const lazyFocus = (arg) => {
    toFocus.value = arg
  }

  // focusing a record that is about to arrive: a record just created shows up
  // either through a socket creation event, which only appends it to the list,
  // or through a reload. lazyFocus is read on reloads alone, so this watches the
  // list instead and focuses the record once it is there. A newer call replaces
  // a pending one, and the wait is given up after a while so a record that
  // never shows up cannot pull the view away later.
  const FOCUS_WHEN_PRESENT_TIMEOUT_MS = 60_000
  let cancelPendingFocus = null
  const focusWhenPresent = (arg) => {
    cancelPendingFocus?.()
    cancelPendingFocus = null
    const present = () => records().some((record) => record[key] === arg[key])
    if (present()) {
      focus(arg)
      return
    }
    const stop = watch(records, () => {
      if (!present()) return
      cancel()
      focus(arg)
    })
    const timer = setTimeout(() => cancel(), FOCUS_WHEN_PRESENT_TIMEOUT_MS)
    const cancel = () => {
      stop()
      clearTimeout(timer)
      if (cancelPendingFocus === cancel) cancelPendingFocus = null
    }
    cancelPendingFocus = cancel
  }

  // lazy multi-selection: a set of ids to select once the records that carry
  // them have loaded. Counterpart to lazyFocus for multi-select stores; used
  // when applying a target location whose data is not resident yet.
  const toSelect = ref(null)
  const lazySelect = (ids) => {
    toSelect.value = Array.isArray(ids) ? ids : ids != null ? [ids] : null
  }

  // focus logging
  if (singleselect) {
    watch(focused, (nextFocus, prevFocus) => {
      // Compare ids, not object references!
      const prevId = prevFocus ? prevFocus[key] : null
      const nextId = nextFocus ? nextFocus[key] : null
      if (prevId !== nextId) {
        if (prevFocus) {
          logger.debug(`unfocusing`, {
            icon: '☁️',
            data: { record: prevFocus }
          })
        }
        if (nextFocus) {
          logger.debug(`focusing`, {
            icon: '🔍',
            data: { record: nextFocus }
          })
        }
      }
    })
  }

  // selection logging
  if (multiselect) {
    watch(selected, (nextSelected, prevSelected) => {
      // Extract IDs for comparison
      const prevIds = prevSelected.map((p) => p[key])
      const nextIds = nextSelected.map((n) => n[key])

      let icon = '☁️'
      prevSelected.forEach((selected) => {
        const newlyUnselected = !nextIds.includes(selected[key])
        const data = { record: selected }
        if (newlyUnselected) {
          if (nextSelected.length >= 1) {
            logger.debug('unselecting', { icon, data })
          } else {
            logger.log('unfocusing', { icon, data })
          }
        }
      })
      icon = '🔍'
      nextSelected.forEach((selected) => {
        const newlySelected = !prevIds.includes(selected[key])
        const data = { record: selected }
        if (newlySelected) {
          if (!focused.value) {
            logger.debug('selecting', { icon, data })
          }
        }
      })
      if (focused.value) {
        const data = { record: focused.value }
        logger.debug('focusing', { icon, data })
      }
    })
  }

  // persistence
  //
  // A persistent selection is written to localStorage as a JSON array of ids
  // (one element for single-select, N for multi-select) and restored once the
  // store's records have loaded. Restoration validates each id against the
  // freshly loaded records: unknown ids are dropped, and the whole entry is
  // discarded only when records exist yet none of the stored ids survive. An
  // empty record set means deps are not met yet, so the entry is kept and
  // retried on the next load.

  const stateLoaded = ref(false)
  const storageKey = `module[${name}]`

  const restoreState = () => {
    if (stateLoaded.value || !persist) return false

    const raw = localStorage.getItem(storageKey)
    if (!raw || raw === 'undefined' || raw === 'null') {
      logger.debug('state not found or invalid in storage', { data: { storageKey, raw } })
      return false
    }

    // Decode stored ids. The current format is a JSON array; tolerate a bare
    // id string written by earlier single-select-only persistence.
    let ids
    try {
      const parsed = JSON.parse(raw)
      ids = Array.isArray(parsed) ? parsed : [parsed]
    } catch {
      ids = [raw]
    }
    ids = ids.filter((id) => id != null)
    if (ids.length === 0) return false

    // Records not loaded yet (deps unmet): keep the entry and retry later.
    if (records().length === 0) return false

    // Restore only ids that still exist; drop the entry if none survive.
    const restorable = ids.filter((id) => records().some((record) => record[key] === id))
    if (restorable.length === 0) {
      logger.debug('stored state no longer valid', { data: { storageKey, ids } })
      localStorage.removeItem(storageKey)
      return false
    }

    logger.debug('loading selection from storage', { data: { ids: restorable, storageKey } })
    if (multiselect) {
      selected.value = records().filter((record) => restorable.includes(record[key]))
    } else {
      focus({ [key]: restorable[0] })
    }
    stateLoaded.value = true
    return true
  }

  const persistState = () => {
    const ids = selectedIds.value
    if (ids.length > 0) {
      logger.debug(`saving selection to storage`, { icon: '💾', data: { ids, storageKey } })
      localStorage.setItem(storageKey, JSON.stringify(ids))
    } else {
      logger.debug(`clearing selection from storage`, { icon: '💾', data: { storageKey } })
      localStorage.removeItem(storageKey)
    }
  }

  const resetPersist = () => {
    stateLoaded.value = false
    localStorage.removeItem(storageKey)
  }

  if (persist) {
    // Watch a primitive serialization of the ids, not the `selected` array
    // reference. Refocus after a deps-unmet sync reassigns the selection to a
    // fresh empty array; watching the reference would fire persistState on that
    // empty -> empty churn and wipe the stored id before restoreState can read
    // it back. Serializing means only real content changes persist.
    watch(
      () => selectedIds.value.join(','),
      () => persistState()
    )
  }

  // focus automation

  // automatically reassign next focus after reload
  const prepRefocus = () => () => {
    // Capture previous selection state BEFORE reload (works for both single & multi-select)
    const previousSelectedIds = selected.value.map((s) => s[key])

    // --- scheduled lazy multi-selection takes priority ---
    // Only consume the target once records are present; while the store is
    // empty (deps unmet) keep it queued for a later load.
    if (toSelect.value?.length && records().length > 0) {
      const wanted = toSelect.value
      const recordsToSelect = records().filter((r) => wanted.includes(r[key]))
      toSelect.value = null
      if (recordsToSelect.length > 0) {
        logger.debug(`lazy selecting ${recordsToSelect.length} record(s)`)
        selected.value = recordsToSelect
        return focused.value
      }
    }

    // --- scheduled lazy focus takes priority ---
    const nextId = toFocus.value?.[key]
    const nextValid = records()
      .map((record) => record[key])
      .includes(nextId)
    if (nextId && nextValid) {
      logger.debug(`lazy refocusing on ${nextId}`)
      toFocus.value = null
      focus({ [key]: nextId })
      return focused.value
    }

    // --- Restore previous selection by IDs (both single/multi select) ---
    if (previousSelectedIds.length > 0) {
      const recordsToRestore = records().filter((r) => previousSelectedIds.includes(r[key]))
      if (recordsToRestore.length > 0) {
        logger.debug(`refocusing ${recordsToRestore.length} record(s)`)
        selected.value = recordsToRestore
        return focused.value
      }
    }
    // --- try to restore state from localStorage (only if persist enabled) ---
    const restored = restoreState()
    if (restored) {
      return focused.value
    }

    // --- try to unfocus if allowed ---
    if (allowUnfocus) {
      unfocus()
      return focused.value
    }

    // --- force unfocus when records are empty (even in single-select mode) ---
    if (records().length === 0) {
      unfocus()
      return focused.value
    }

    // --- finally try to autofocus on the first record  as fallback ---
    const resolved = records().length > 0 ? records()[0] : null
    if (!resolved) {
      logger.warn('refocus failed to resolve the default record.')
      return focused.value // Return early when no record to focus
    }
    logger.debug(`autofocusing ${resolved[key]}`)
    focus(resolved)
    return focused.value
  }

  // EVENTS

  // manage socket room subscription
  if (subscribe) {
    let room = (record) => record[key]
    if (typeof subscribe === 'function') {
      room = subscribe
    }
    watch(focused, (next, prev) => {
      // Extract room IDs
      const prevRoom = prev ? room(prev) : null
      const nextRoom = next ? room(next) : null

      // Only unsubscribe/subscribe if room ID actually changed
      if (prevRoom !== nextRoom) {
        if (prevRoom) {
          logger.debug(`unsubscribing from`, {
            icon: '📪',
            data: { room: prevRoom }
          })
          api.socket.removeSubscription(prevRoom)
        }
        if (nextRoom) {
          logger.debug(`subscribing to`, {
            icon: '📬',
            data: { room: nextRoom }
          })
          api.socket.addSubscription(nextRoom)
        }
      }
    })
  }

  // execute custom hook on refocus
  if (hook) {
    watch(focused, async (next, prev) => {
      hook({ next, prev }) // This is a custom callback,
    })
  }

  // API

  return {
    // options
    multiselect,
    singleselect,
    allowUnfocus,
    // selection
    selected,
    selectedIds,
    isSelected,
    select,
    unselect,
    // focus
    focused,
    focusedId,
    focus,
    unfocus,
    prepRefocus,
    lazyFocus,
    focusWhenPresent,
    lazySelect,
    resetPersist
  }
}
