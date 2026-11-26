import { ref } from 'vue'
import { defineStore } from 'pinia'

import { isRunInFlight } from '@/lib/runStatus'

/**
 * Whether the per-sample "Assign sample" configuration dialog is open, and
 * which sample a launch is on its way for.
 *
 * The button that opens it now sits in the browser's switch bar
 * (AssignmentRunBar.vue), while the dialog, the run configuration, the launch
 * itself and the refusal it may come back with stay with the ledger the run
 * fills (PaneBrowserAssignment.vue). The two are siblings under
 * PaneBrowserMatch, so the state they share cannot travel as a prop.
 *
 * Deliberately only state: keeping `launch()` and its error message beside
 * the ledger is what puts a refusal next to the table it explains, and keeps
 * every launcher behaviour testable from one mount.
 */
export const useAssignmentLauncher = defineStore('browser.match.assignment.launcher', () => {
  const configVisible = ref(false)
  // The sample whose launch is in flight: from the click on Assign until the
  // run list has the new run in it, which is the moment the run's own status
  // takes over saying so. Null when no launch is on its way.
  const launchingId = ref(null)

  return { configVisible, launchingId }
})

export const ASSIGN_IN_FLIGHT_REASON =
  "This sample's assignment run is in progress. Assign it again once the run ends."
export const ASSIGN_LAUNCHING_REASON = 'Starting the assignment run…'

/**
 * Why Assign sample cannot start a run now, or null when it can.
 *
 * A run of the focused sample that is still in flight holds the sample: the
 * server refuses a second one as busy, so the button is not offered. A launch
 * already on its way for the sample counts the same, so a second click before
 * the run list has caught up sends nothing. The run list holds the focused
 * sample's runs only, so moving to another sample answers for that one.
 *
 * @param {object} app the app store (`useApp()`)
 * @param {object} launcher the launcher store (`useAssignmentLauncher()`)
 * @returns {string|null} the reason, worded for a tooltip
 */
export function assignBlockedReason(app, launcher) {
  const sampleId = app.data.sample.focusedId ?? app.data.sample.focused?.sample_item_id ?? null
  if (sampleId != null && launcher.launchingId === sampleId) return ASSIGN_LAUNCHING_REASON
  const runs = app.data.peakAssignment.run.list ?? []
  if (runs.some(isRunInFlight)) return ASSIGN_IN_FLIGHT_REASON
  return null
}
