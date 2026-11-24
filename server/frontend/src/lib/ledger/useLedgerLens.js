import { reactive } from 'vue'
import { defineStore } from 'pinia'

/**
 * The lens on the rows a reading spans, per scope
 * (docs/dev/assignment_ledger_and_card.md, section 3.5): the group the ledger
 * is narrowed to, or none.
 *
 * Held outside the panes, so the card's *Show in the ledger* can open it on a
 * ledger it does not draw, and so it survives the pane's swap. A lens names
 * what it was opened on - the batch, and in the sample scope the sample - and
 * a pane shows it only while that is still what it lists: a lens of another
 * batch or sample is closed rather than drawn over the wrong rows.
 *
 * `sorted` is set when the reader sorts while the lens is open: the members
 * then follow the column, where until then they read in the group's own
 * order.
 */
export const useLedgerLensStore = defineStore('browser.match.ledgerLens', () => {
  const lenses = reactive({ sample: null, batch: null })

  /**
   * Narrow a scope's ledger to a group.
   * @param {'sample'|'batch'} scope
   * @param {object} group - a `batch_groups` entry, or a sample's own channels
   * @param {{batchId?: string|null, sampleId?: string|null}} [on] - what the
   *   lens is opened on
   */
  function open(scope, group, on = {}) {
    if (!group) return
    lenses[scope] = {
      group,
      batchId: on.batchId ?? null,
      sampleId: on.sampleId ?? null,
      sorted: false
    }
  }

  /** Close a scope's lens: the ledger lists what its view chose again. */
  function close(scope) {
    lenses[scope] = null
  }

  /** Mark that the reader sorted under the lens. */
  function sortedUnder(scope) {
    if (lenses[scope]) lenses[scope].sorted = true
  }

  return { lenses, open, close, sortedUnder }
})
