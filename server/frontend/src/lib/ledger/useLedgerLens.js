import { reactive } from 'vue'
import { defineStore } from 'pinia'

import { useLedgerViewStore } from './useLedgerView.js'

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
 * order. Such a sort is the lens's alone: the scope's sort as it stood when
 * the first lens opened is kept beside it, and closing the lens puts it back,
 * so × and Escape give back the view the reader left.
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
    // A lens opened over another on the same ledger keeps the sort the first
    // one found; one left on another batch or sample does not, as the reader
    // may have sorted since on the ledger it no longer covers.
    const view = useLedgerViewStore()[scope]
    const previous = lenses[scope]
    const same =
      previous &&
      previous.batchId === (on.batchId ?? null) &&
      previous.sampleId === (on.sampleId ?? null)
    const before = same ? previous.sortBefore : { field: view.field, order: view.order }
    lenses[scope] = {
      group,
      batchId: on.batchId ?? null,
      sampleId: on.sampleId ?? null,
      sorted: false,
      sortBefore: before
    }
  }

  /**
   * Close a scope's lens: the ledger lists what its view chose again, sorted
   * as it was before the lens opened.
   */
  function close(scope) {
    const state = lenses[scope]
    lenses[scope] = null
    if (!state?.sortBefore) return
    const view = useLedgerViewStore()[scope]
    view.field = state.sortBefore.field
    view.order = state.sortBefore.order
  }

  /** Mark that the reader sorted under the lens. */
  function sortedUnder(scope) {
    if (lenses[scope]) lenses[scope].sorted = true
  }

  return { lenses, open, close, sortedUnder }
})
