import { defineStore } from 'pinia'

import { MAX_SELECTED_BATCH_PEAKS } from '@/lib/batchChart'
import { useApp } from '@/stores'

/**
 * Plot a sample ledger row's species in the batch chart, or take it out.
 *
 * The batch chart draws the Batch peaks ledger's selection, so this is that
 * selection edited from the sample ledger one species at a time: the row's
 * peak folded into a batch peak (`batch_peak_id`, looked up on read for a
 * run's row and carried by a derived one), and that batch peak is what gets
 * selected. The ledger's cap holds here as it does in the Batch peaks pane -
 * the chart draws at most `MAX_SELECTED_BATCH_PEAKS` species - and a species
 * the ledger has not loaded is said rather than silently skipped.
 */
export const useBatchChartSelection = defineStore('browser.match.batchChartSelection', () => {
  const app = useApp()
  const ledger = () => app.data.batchPeak

  const notify = (message, status = 'warning') =>
    app.ui.notification.push({ type: 'batch_chart_selection', message, status })

  /** Whether the row's peak is in the batch ledger at all. */
  const canPlot = (row) => Boolean(row?.batch_peak_id)

  /** Whether the row's species is in the batch chart now. */
  const isPlotted = (row) =>
    canPlot(row) && Boolean(ledger()?.isSelected({ batch_peak_id: row.batch_peak_id }))

  /**
   * Add the row's species to the batch chart, or remove it when it is there.
   * @returns {boolean} whether the selection changed
   */
  function toggle(row) {
    if (!canPlot(row)) return false
    const key = { batch_peak_id: row.batch_peak_id }
    const store = ledger()
    if (store.isSelected(key)) {
      store.unselect(key)
      return true
    }
    if (!store.list.some((bp) => bp.batch_peak_id === row.batch_peak_id)) {
      notify('The batch ledger has not loaded this species yet; open the Batch peaks tab first.')
      return false
    }
    if (store.selected.length >= MAX_SELECTED_BATCH_PEAKS) {
      notify(
        `The batch chart is full at ${MAX_SELECTED_BATCH_PEAKS} species; ` +
          'take one out in the Batch peaks ledger first.'
      )
      return false
    }
    store.select(key)
    return true
  }

  return { canPlot, isPlotted, toggle }
})
