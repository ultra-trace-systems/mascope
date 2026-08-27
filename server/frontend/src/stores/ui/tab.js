import { ref, watch } from 'vue'
import { defineStore } from 'pinia'

import { useData } from '../data'

const DEFAULT_TAB = 'raw files'

// Safety cap on how long automatic tab switching stays suppressed after a
// location restore. Normally the location store lifts the suppression itself
// via endHydrate() once the restored chain settles; this only bounds the case
// where that never happens (e.g. the chain never fully converges).
const HYDRATE_MAX_MS = 20000

export const useTab = defineStore('app.ui.tab', () => {
  const active = ref(DEFAULT_TAB)

  // While hydrating a restored location, the automatic tab watchers below stand
  // down so the tab we are restoring is the final word.
  const hydrating = ref(false)
  let hydrateTimer = null

  const data = useData()

  // When visualized ion unfocused, return to 'batch' if batch exists, else default.
  // Runs whatever the peak_assignment flag says: the Match tab is always
  // rendered, so visualizing an ion always has somewhere to go.
  watch(
    () => data.match.visualized.ion,
    (visualized) => {
      if (hydrating.value) return
      if (!visualized && active.value === 'match') {
        active.value = data.batch.focused ? 'batch' : DEFAULT_TAB
      } else if (visualized && active.value !== 'sample') {
        active.value = 'match'
      }
    }
  )

  // Return to 'batch' tab when sample unfocused (if currently on sample tab)
  watch(
    () => data.sample.focused,
    (sample) => {
      if (hydrating.value) return
      if (!sample && active.value === 'sample') {
        active.value = 'batch'
      }
    }
  )
  // Switch to batch tab when samples INITIALLY loaded (0 → N) AND batch focused
  // Leave batch tab when samples become empty
  watch(
    () => data.sample.list.length,
    (length, oldLength) => {
      if (hydrating.value) return
      if (oldLength === 0 && length > 0 && data.batch.focused) {
        active.value = 'batch'
      } else if (length === 0 && active.value === 'batch') {
        // Samples empty → leave batch tab
        active.value = DEFAULT_TAB
      }
    }
  )

  // Return to default tab when batch unfocused (if currently on batch tab)
  watch(
    () => data.batch.focused,
    (focused) => {
      if (hydrating.value) return
      if (!focused && active.value === 'batch') {
        active.value = DEFAULT_TAB
      }
    }
  )

  return {
    active,
    hydrating,
    default: () => {
      active.value = DEFAULT_TAB
    },
    /**
     * Restore a tab as part of a location apply: set it and suppress automatic
     * tab switching so streaming data does not move it away. The location store
     * calls endHydrate() once the chain settles; the timer is only a safety cap.
     */
    hydrate: (tab) => {
      if (!tab) return
      hydrating.value = true
      active.value = tab
      clearTimeout(hydrateTimer)
      hydrateTimer = setTimeout(() => {
        hydrating.value = false
      }, HYDRATE_MAX_MS)
    },

    /** Lift the hydrate suppression (chain settled). */
    endHydrate: () => {
      clearTimeout(hydrateTimer)
      hydrating.value = false
    }
  }
})
