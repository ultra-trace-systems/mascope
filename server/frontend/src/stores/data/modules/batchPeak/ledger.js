import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'

import { useBatch } from '../batch'
import { useBatchPeakRun } from './run'

/**
 * Ceiling on how many batch peaks may be selected at once.
 *
 * The ledger below is deliberately unbounded and its tail grows with the batch,
 * so "select all" over it is a gesture with no natural size. Everything the
 * selection touches is priced per selected record - a Plotly trace and a legend
 * entry each, a share of a series request, and, in the shared selection
 * plumbing, a log line and a linear scan - so an unbounded selection is an
 * unbounded amount of work performed on one click. The cap is what lets the
 * ledger stay exhaustive without letting its tail reach any of that; filtering
 * by tier or formula is how the user chooses *which* few hundred.
 *
 * Enforced where the table writes its selection (PaneBrowserBatchPeaks.vue) and
 * again where the chart reads it, since the chart has its own way in.
 */
export const MAX_SELECTED_BATCH_PEAKS = 300

/**
 * Ledger of a batch's "batch peaks" (cross-sample m/z anchors) -- the selection
 * surface for the peak-centric batch overview. Multi-select here drives which
 * batch peaks the Assignments chart plots, up to MAX_SELECTED_BATCH_PEAKS, so
 * the chart never renders 1000+ traces at once. Metadata only (no per-sample
 * series); the chart fetches the series for the selected peaks.
 */
export const useBatchPeakLedger = defineStore('app.data.batchPeak', () => {
  const name = 'batch_peak'
  const key = 'batch_peak_id'

  const data = useData(
    name,
    ({ sample_batch_id, batch_peak_run_id }) => {
      if (!sample_batch_id) return []
      // min_n_present=1: the ledger lists every batch peak so any (even
      // event-specific, low-prevalence) species is selectable; the selection is
      // what limits the plot, and the selection is capped rather than the list.
      return api.http.get(`/batch-peaks/batch/${sample_batch_id}`, {
        // An earlier run reads off its snapshot; the live ledger names no run.
        params: { min_n_present: 1, ...(batch_peak_run_id ? { batch_peak_run_id } : {}) },
        use: 'read',
        type: 'load_batch_peak_ledger'
      })
    },
    {
      key,
      deps: () => ({
        sample_batch_id: useBatch().focusedId,
        // An earlier run's ledger is read off its snapshot. The live ledger is
        // undefined here rather than null: a null dependency is one the loader
        // waits on, and there is no run to wait for.
        batch_peak_run_id: useBatchPeakRun().viewingId ?? undefined
      }),
      selection: { mode: 'multiple' },
      // Reload when the arrival fold-in / backfill updates batch peaks.
      events: ['peak_assignment_reload']
    }
  )

  // No tier histogram here. The strip it feeds counts SPECIES rather than
  // anchors, and which anchors are species is decided by resolving the
  // `isotopologue_of` links against the whole loaded list - chains onto their root,
  // a link out of the list back to top level. That resolution lives in the pane
  // (PaneBrowserBatchPeaks.vue), which is also what renders the rows the chips
  // filter, so the count and the rows it promises come from one rule.
  return { ...data }
})
