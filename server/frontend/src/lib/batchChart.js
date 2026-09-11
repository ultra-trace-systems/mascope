/**
 * How many batch peaks the batch chart draws at once - the cap on the Batch
 * peaks ledger's selection, which is what the chart plots.
 *
 * Kept in a module of its own, without imports, because the number is read in
 * three places that must not pull the ledger store in with it: the store that
 * defines the selection, the chart that reads it, and the sample ledger's
 * action that edits it from the other side. The reasoning behind the cap is
 * with the ledger store (stores/data/modules/batchPeak/ledger.js).
 */
export const MAX_SELECTED_BATCH_PEAKS = 300

/**
 * What the batch chart calls a batch peak: its consensus formula, or its m/z
 * when the ledger has no formula for it. The chart's legend and the filter chip
 * for the ledger's selection both read it, so the two name a species alike.
 */
export function batchPeakLabel(record) {
  return record.consensus_formula || `m/z ${Number(record.mz).toFixed(4)}`
}
