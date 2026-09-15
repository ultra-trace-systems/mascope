import { ref } from 'vue'
import { defineStore } from 'pinia'

/**
 * Whether the per-sample "Assign peaks" configuration dialog is open.
 *
 * The button that opens it now sits in the browser's switch bar
 * (AssignmentRunBar.vue), while the dialog, the run configuration, the launch
 * itself and the refusal it may come back with stay with the ledger the run
 * fills (PaneBrowserAssignment.vue). The two are siblings under
 * PaneBrowserMatch, so the flag they share cannot travel as a prop.
 *
 * Deliberately only the flag: keeping `launch()` and its error message beside
 * the ledger is what puts a refusal next to the table it explains, and keeps
 * every launcher behaviour testable from one mount.
 */
export const useAssignmentLauncher = defineStore('browser.match.assignment.launcher', () => {
  const configVisible = ref(false)

  return { configVisible }
})
