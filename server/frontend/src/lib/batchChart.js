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
 * The instrument types a batch's ledger can be partitioned into, in the order
 * the ledger offers them, with what the app calls each. An Orbitrap member's
 * intensity is a height and a TOF member's an area, so the fold keeps each
 * type's batch peaks apart, every row names its own (`instrument_type`), and
 * the chart draws one type at a time.
 */
export const INSTRUMENT_TYPE_LABELS = { orbi: 'Orbitrap', tof: 'TOF' }

/**
 * What the batch chart calls a batch peak: its consensus formula, or its m/z
 * when the ledger has no formula for it. The chart's legend and the filter chip
 * for the ledger's selection both read it, so the two name a species alike.
 */
export function batchPeakLabel(record) {
  return record.consensus_formula || `m/z ${Number(record.mz).toFixed(4)}`
}
