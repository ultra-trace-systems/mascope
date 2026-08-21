import { ref, shallowRef, computed, watch, onMounted, onUnmounted } from 'vue'

import { makeLogger } from '@/lib/logging'

import { useAuth } from '@/stores/auth'
import { useEvents } from './events'
import { useLoader } from './loader'
import { useSelection } from './selection'

export const useData = (
  // required
  name,
  method,
  // optional
  options = {}
) => {
  // CONFIG

  // destructure options and set defaults
  const { key, events, deps, read, detailed } = {
    key: `${name}_id`,
    events: [], // Only for cross-store reload events (e.g., match_reload for sample store)
    deps: null,
    ...options
  }

  // logging
  const logger = makeLogger({
    prefix: `data ${name}`,
    icon: '🗃️'
  })

  // State
  const records = shallowRef([]) // private
  const pending = ref(false)
  const error = ref(null) // last sync failure, cleared on the next attempt
  const list = computed(() => records.value) // read-only data

  // Selection - conditionally initialize
  const selection = options?.selection
    ? useSelection(
        name,
        key,
        () => records.value,
        options.selection === true
          ? {} // use defaults if set to true
          : options.selection // pass options otherwise
      )
    : null

  // filtering
  const filtered = computed(() =>
    selection?.selected && selection.selected.value.length > 0
      ? selection.selected.value
      : list.value
  )
  const filteredIds = computed(() => filtered.value.map((record) => record[key]))

  // Data loading
  const { sync, reloadRecord, load } = useLoader(
    name,
    key,
    method,
    { records, pending, error, selection, detailed },
    { deps, read },
    logger
  )

  // Socket events
  const { cleanup: cleanupEvents } = useEvents(
    name,
    key,
    { records, error, selection, detailed },
    { sync, reloadRecord },
    events,
    logger,
    deps
  )

  // Initialization, all root stores initialize themselves
  onMounted(() => {
    const auth = useAuth()
    auth.onLogin(() => {
      sync({ context: 'initialization' })
    })
  })

  // reload when dependencies change
  if (deps) {
    watch(deps, (next, prev) => {
      if (JSON.stringify(next) !== JSON.stringify(prev)) {
        // Find and log changes
        Object.keys({ ...prev, ...next })
          .filter((key) => JSON.stringify(next?.[key]) !== JSON.stringify(prev?.[key]))
          .forEach((key) => {
            logger.debug(`dependency change for ${key}: ${prev?.[key]} -> ${next?.[key]}`, {
              icon: '🔄',
              data: { key, from: prev?.[key], to: next?.[key] }
            })
          })

        // Clear persisted selection when the parent genuinely switches
        // (not on initial load where prev is null).
        const prevHadValue = prev && Object.values(prev).some((v) => v != null)
        if (prevHadValue) {
          selection?.resetPersist?.()
        }

        sync({ context: 'dependencies' })
      }
    })
  }

  // Cleanup on unmount
  onUnmounted(cleanupEvents)

  // API
  return {
    list,
    pending,
    error,
    load,
    filtered,
    filteredIds,
    // Only expose if provided
    ...(detailed && { detailed }),
    ...(selection ?? {})
  }
}
