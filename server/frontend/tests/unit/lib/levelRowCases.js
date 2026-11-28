// The rows the one level rule is pinned on (lib/levels.js, rowLevel), shared by
// the sample ledger's spec and the inspector card's, so the two are read on the
// same rows and must show the same level and the same mark
// (docs/dev/assignment_ledger_and_card.md, section 4.2).

/** The batch peak's own level, as a row the batch ledger serves carries it. */
export const BATCH_PEAK_LEVEL = Object.freeze({
  level: '4a',
  evidence: 'rivals out by the batch Br lock; 3 of 4 members read 4a',
  lift: 'a library spectrum match',
  batch_peak_id: 'bp-1',
  batch_level: '4a'
})

/** A person's batch-level verdict stating level 1. */
export const BATCH_CLAIM = Object.freeze({
  verdict: 'confirmed',
  evidence_level: 'reference_standard',
  level: 1,
  level_letter: null,
  claimed_inchikey: 'XLYOFNOQVPJJNP-UHFFFAOYSA-N',
  context: { claim: { evidence: 'a reference standard matched', lift: null } }
})

/** The same claim served to a sample its window leaves out. */
export const OUTSIDE_WINDOW = Object.freeze({
  ...BATCH_CLAIM,
  level: null,
  claimed_inchikey: null,
  outside_window: true
})

const RUN_LEVEL = { level: '5a', level_evidence: 'one rival open', level_lift: 'exclude it' }
const SERVED = { level: null, level_evidence: null, level_lift: null }

/**
 * Each case: the row's level fields, whether the batch ledger serves it, the
 * batch-level verdict reaching it, the detail's `batch_level` where the case
 * has one, and what both readers must show (null: no level).
 */
export const LEVEL_ROW_CASES = Object.freeze([
  {
    name: "a run's row",
    row: { ...RUN_LEVEL },
    served: false,
    batchVerdict: null,
    shows: { level: '5a', source: 'engine', fromBatch: false }
  },
  {
    name: "a run's row the batch lifts",
    row: { ...RUN_LEVEL },
    served: false,
    batchVerdict: null,
    detailBatchLevel: { level: '4a', stated: '5a', borrowed: true, evidence: 'the lock' },
    shows: { level: '5a', source: 'engine', fromBatch: false }
  },
  {
    name: "a run's row a batch-level claim reaches",
    row: { ...RUN_LEVEL },
    served: false,
    batchVerdict: BATCH_CLAIM,
    shows: { level: '1', source: 'claim', fromBatch: true }
  },
  {
    name: 'a served row with a live batch verdict',
    row: { ...SERVED, batch_level: BATCH_PEAK_LEVEL },
    served: true,
    batchVerdict: BATCH_CLAIM,
    shows: { level: '1', source: 'claim', fromBatch: true }
  },
  {
    name: "a served row with only the batch peak's level",
    row: { ...SERVED, batch_level: BATCH_PEAK_LEVEL },
    served: true,
    batchVerdict: null,
    shows: { level: '4a', source: 'batch', fromBatch: true }
  },
  {
    name: 'a served row with neither',
    row: { ...SERVED, batch_level: null },
    served: true,
    batchVerdict: null,
    shows: null
  },
  {
    name: 'a level-1 batch claim whose window leaves the served sample out',
    row: { ...SERVED, batch_level: BATCH_PEAK_LEVEL },
    served: true,
    batchVerdict: OUTSIDE_WINDOW,
    shows: { level: '4a', source: 'batch', fromBatch: true }
  },
  {
    name: "a level-1 batch claim whose window leaves a run's row out",
    row: { ...RUN_LEVEL },
    served: false,
    batchVerdict: OUTSIDE_WINDOW,
    shows: { level: '5a', source: 'engine', fromBatch: false }
  }
])
