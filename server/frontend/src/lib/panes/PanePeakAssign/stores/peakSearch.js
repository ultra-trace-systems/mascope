import { ref } from 'vue'
import { defineStore } from 'pinia'

/**
 * Whether the Sample view shows the formula search for the focused peak in
 * place of the assignment time series: the inspector's "Find other formulas
 * for this peak" turns it on and off, and so does the sample ledger's Curate
 * action on a row (docs/dev/assignment_ledger_and_card.md, section 3.3). A
 * store rather than the Sample view's own state, since the ledger that opens
 * it is in another tab's pane. The Sample view closes it when it goes, as its
 * own state did.
 */
export const usePeakSearch = defineStore('panes.peakAssign.peakSearch', () => {
  const open = ref(false)
  return { open }
})
