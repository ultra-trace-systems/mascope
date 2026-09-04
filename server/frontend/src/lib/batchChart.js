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
