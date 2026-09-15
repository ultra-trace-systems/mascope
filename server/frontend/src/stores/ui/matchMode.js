import { ref, watch } from 'vue'
import { defineStore } from 'pinia'

import { peakAssignmentEnabled } from '@/lib/features'

/**
 * The Targets/Assignments choice, shared by everything that renders one of the
 * two coexisting paradigms: the browser shell's panes
 * (`PaneBrowserMatch.vue`) and the batch overview chart (`PaneTabBatch.vue`).
 *
 * It used to be a component-local ref in each -- the browser persisted its
 * choice and the batch tab did not, so the batch chart came back on Targets on
 * every page load while the browser came back where it was left, and either
 * could be flipped without the other moving. That mattered because the
 * assignments chart plots exactly what the batch-peaks ledger has selected, so
 * the browser sitting in Assignments while the chart showed Targets meant the
 * ledger was driving a chart that was not on screen. See
 * docs/dev/peak_assignment_frontend.md.
 */

// Kept from when the browser owned the choice alone, so a user's stored
// preference survives the consolidation.
const MODE_KEY = 'mascope.browserMatch.mode'
const DEFAULT_MODE = 'targets'

/** The two paradigms, in switch order. */
export const MODE_OPTIONS = [
  { label: 'Targets', value: 'targets' },
  { label: 'Assignments', value: 'assignments' }
]

export const useMatchMode = defineStore('app.ui.matchMode', () => {
  // With peak-centric assignment off there is only the targeted view, so the
  // switch is hidden and the mode is pinned regardless of any stored
  // preference. The flag is read inside the setup rather than at module scope
  // so it is decided per store construction.
  const stored = localStorage.getItem(MODE_KEY)
  const known = MODE_OPTIONS.some(({ value }) => value === stored)
  const mode = ref(peakAssignmentEnabled && known ? stored : DEFAULT_MODE)

  // An unrecognised stored value falls back to the default above rather than
  // being carried: one consumer branches on `=== 'assignments'` and the other
  // on `=== 'targets'`, so an unknown mode would put them on opposite sides --
  // the very split this store exists to prevent.

  // Persisted only while the feature is on. A flag-off session pins the value,
  // and writing that back would erase the choice a flag-on session made.
  watch(mode, (value) => {
    if (peakAssignmentEnabled) localStorage.setItem(MODE_KEY, value)
  })

  return {
    mode,
    options: MODE_OPTIONS
  }
})
