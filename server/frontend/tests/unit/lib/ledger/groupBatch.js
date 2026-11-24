// The fixture batch of the groups' lens (MAS-1069), as the batch-peaks read
// serves it: the batch the backend's groups suite seeds
// (server/backend/tests/integration/api/peak_assignments/test_batch_groups_api.py,
// MAS-1066), so the server's derivation and the client's ordering are proven
// on one batch.
//
// - A CH2 series from an assigned root (C5H8O4) through one mechanism: one
//   step down and three up proposed, one step up assigned, and no batch peak
//   two steps up - a gap.
// - The neutral one step up read through a second mechanism too: two channels.
// - A co-variation record on each channel naming the other: one pair.
// - The root's batch checks naming its 13C line (halogen lock) and a 14N twin.
// - An isotope line of the member one step up, which no group lists.

export const NITRATE = 'mech-grp-no3'
export const DEPROTONATED = 'mech-grp-mh'
export const NITRATE_NOTATION = '[M+NO3]-'
export const DEPROTONATED_NOTATION = '[M-H]-'

const notation = { [NITRATE]: NITRATE_NOTATION, [DEPROTONATED]: DEPROTONATED_NOTATION }

/** The anchors, by name: formula, ion, mechanism, m/z, tier. */
export const ANCHORS = {
  down: ['C4H6O4', 'C4H6NO7-', NITRATE, 180.0, 'candidate'],
  root: ['C5H8O4', 'C5H8NO7-', NITRATE, 194.0, 'assigned'],
  up: ['C6H10O4', 'C6H10NO7-', NITRATE, 208.0, 'assigned'],
  up_line: ['C6H10O4', '[13C]C5H10NO7-', NITRATE, 209.0, 'assigned'],
  up_other: ['C6H10O4', 'C6H9O4-', DEPROTONATED, 145.05, 'candidate'],
  two_other: ['C7H12O4', 'C7H11O4-', DEPROTONATED, 159.07, 'candidate'],
  three: ['C8H14O4', 'C8H14NO7-', NITRATE, 236.0, 'candidate'],
  twin: ['C5H9NO4', 'C5H8NO4-', DEPROTONATED, 146.05, 'candidate'],
  root_line: ['C5H8O4', '[13C]C4H8NO7-', NITRATE, 195.0, 'assigned']
}

/** A batch peak's id in the fixture. */
export const idOf = (name) => `bp-${name}`

/** The batch peaks as the ledger's rows carry them. */
export const BATCH_PEAKS = Object.entries(ANCHORS).map(
  ([name, [formula, ion, mechanism, mz, tier]]) => ({
    batch_peak_id: idOf(name),
    mz,
    consensus_formula: formula,
    consensus_ion_formula: ion,
    ionization_mechanism_id: mechanism,
    consensus_tier: tier,
    n_present: 1,
    isotopologue_of: name === 'up_line' ? idOf('up') : name === 'root_line' ? idOf('root') : null
  })
)

/** One member of a group, as `batch_series.group_member` serves it. */
export function member(name, extra = {}) {
  const [formula, ion, mechanism, mz, tier] = ANCHORS[name]
  return {
    batch_peak_id: idOf(name),
    formula,
    ion_formula: ion,
    ionization_mechanism_id: mechanism,
    mechanism: notation[mechanism],
    mz,
    consensus_tier: tier,
    sample_peak_id: null,
    ...extra
  }
}

export const SERIES_GROUP = {
  group_id: `series:${idOf('root')}:CH2:${NITRATE}`,
  kind: 'series',
  unit: 'CH2',
  ionization_mechanism_id: NITRATE,
  root_batch_peak_id: idOf('root'),
  root_formula: 'C5H8O4',
  members: [
    member('down', { step: -1 }),
    member('root', { step: 0 }),
    member('up', { step: 1 }),
    member('three', { step: 3 })
  ]
}

export const CHANNEL_GROUP = {
  group_id: 'channels:C6H10O4',
  kind: 'channels',
  formula: 'C6H10O4',
  members: [member('up_other'), member('up')]
}

export const CO_VARIATION_GROUP = {
  group_id: `co_variation:${idOf('up_other')}`,
  kind: 'co_variation',
  anchor_batch_peak_id: idOf('up_other'),
  members: [member('up_other'), member('up')]
}

export const HALOGEN_GROUP = {
  group_id: `check_pair:halogen_lock:${idOf('root')}`,
  kind: 'check_pair',
  check: 'halogen_lock',
  anchor_batch_peak_id: idOf('root'),
  members: [member('root'), member('root_line')]
}

export const TWIN_GROUP = {
  group_id: `check_pair:label_twin:${idOf('root')}`,
  kind: 'check_pair',
  check: 'label_twin',
  anchor_batch_peak_id: idOf('root'),
  members: [member('root'), member('twin')]
}

/** The batch's groups, in the order the read lists them. */
export const BATCH_GROUPS = [
  SERIES_GROUP,
  CHANNEL_GROUP,
  CO_VARIATION_GROUP,
  HALOGEN_GROUP,
  TWIN_GROUP
]
