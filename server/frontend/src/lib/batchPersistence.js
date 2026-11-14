/**
 * What the batch ledger says of a batch peak "Search untargeted" took because
 * its peak recurs across the batch, not because of its height.
 *
 * The search takes a peak present in more of its partition's spectra than the
 * batch's own split of their occurrence distribution, however faint (step 4.2b
 * of the assignment plan, `batch_untargeted.py`). Where the run's intensity
 * floor or peak cap would have left the peak out, the batch peak carries the
 * admission as `persistence` (the sample's row as `batch_persistence`): the
 * `share` of its partition's spectra the peak is in, `n_present` of
 * `n_samples`, the `split` it cleared, and the reading the search elected
 * (`formula`). Persistence says an ion is there, not which formula it is, so
 * that reading is capped at candidate until a line of its isotope pattern,
 * another channel or a series of the batch sees it; the batch peak's
 * `tier_reasons` say which, in the vocabulary of a run's own reasons. The cap
 * never promotes: a reading the samples' vote put below candidate stays
 * there, so what the cap did is read off the batch peak's tier, as a run's
 * reasons are (`reasonTooltip`).
 */

import { holdsTierDown, reasonTooltip, tierReasonsOf } from '@/lib/tierReasons'

/** The rule the persistence admission holds a reading at candidate by. */
export const PERSISTENCE_ONLY = 'persistence_only'

const percent = (share) =>
  typeof share === 'number' && Number.isFinite(share) ? `${Math.round(share * 100)} %` : '?'

const spectra = (record) => `${record.n_present ?? '?'} of ${record.n_samples ?? '?'} spectra`

/**
 * The reasons a batch peak's own rules recorded, named as a run's are.
 *
 * @param {Array|null} reasons - a batch peak's `tier_reasons`
 * @returns {Array} as `tierReasonsOf` names them; empty for none
 */
export function batchTierReasons(reasons) {
  return tierReasonsOf({ tier_reasons: reasons })
}

/**
 * The reasons that hold a batch peak's tier down: what the tier cell's mark
 * says. Empty where none does.
 *
 * @param {Array|null} reasons - a batch peak's `tier_reasons`
 * @returns {Array}
 */
export function holdingReasons(reasons) {
  return batchTierReasons(reasons).filter((reason) => reason.caps)
}

/**
 * The tier cell mark's hover text: for each reason that holds the tier, what
 * it did at the batch peak's tier, its name and the server's sentence.
 *
 * @param {Array|null} reasons - a batch peak's `tier_reasons`
 * @param {string|null} tier - the batch peak's `consensus_tier`
 * @returns {string} '' where none holds it
 */
export function holdingTooltip(reasons, tier) {
  return holdingReasons(reasons)
    .map((reason) => `${reasonTooltip(reason, tier)} (${reason.label}): ${reason.detail}.`)
    .join(' ')
}

/**
 * Whether the persistence rule stands on the reading the search elected:
 * nothing besides its peak has seen it. What that does to the reading is
 * `persistenceHold`'s.
 *
 * @param {Array|null} reasons - a batch peak's or a row's `tier_reasons`
 * @returns {boolean}
 */
export function persistenceStands(reasons) {
  return batchTierReasons(reasons).some(
    (reason) => reason.rule === PERSISTENCE_ONLY && holdsTierDown(reason)
  )
}

/**
 * What the persistence rule does to the reading, read off the batch peak's
 * tier: `held` where it holds it at candidate, `lower` where the samples'
 * vote already put it below, '' where the rule does not stand.
 *
 * @param {Array|null} reasons - a batch peak's or a row's `tier_reasons`
 * @param {string|null} tier - the batch peak's `consensus_tier`
 * @returns {'held'|'lower'|''}
 */
export function persistenceHold(reasons, tier) {
  if (!persistenceStands(reasons)) return ''
  return tier === 'below_assignability' ? 'lower' : 'held'
}

const HOLD_NOTES = Object.freeze({
  held: 'held at candidate',
  lower: 'already below candidate'
})

/**
 * The few words the inspector adds to the share: what the rule does to the
 * reading (`persistenceHold`).
 *
 * @param {Array|null} reasons - a row's `tier_reasons`
 * @param {string|null} tier - the batch peak's `consensus_tier`
 * @returns {string} '' where the rule does not stand
 */
export function persistenceHoldNote(reasons, tier) {
  return HOLD_NOTES[persistenceHold(reasons, tier)] ?? ''
}

/**
 * The one line the inspector and the mark's label read: the share the
 * admission rested on.
 *
 * @param {object|null} record - `persistence`, or a row's `batch_persistence`
 * @returns {string} '' for no record
 */
export function persistenceSummary(record) {
  if (!record) return ''
  return `Searched on persistence: in ${spectra(record)} (${percent(record.share)})`
}

/**
 * The full account: why the search took the peak, what it read it as, and
 * what that reading stands on.
 *
 * @param {object|null} record - `persistence`, or a row's `batch_persistence`
 * @param {Array|null} reasons - the batch peak's `tier_reasons`; on a row, its
 *   record's own (null where the row reads the peak another way)
 * @param {string|null} tier - the batch peak's `consensus_tier`; on a row, its
 *   record's own
 * @returns {string} '' for no record
 */
export function persistenceTooltip(
  record,
  reasons = record?.tier_reasons ?? null,
  tier = record?.consensus_tier ?? null
) {
  if (!record) return ''
  const lines = [
    `The batch search took this peak because it is in ${spectra(record)} of its ` +
      `partition (${percent(record.share)}), above the batch's split at ` +
      `${percent(record.split)}, not because of its height.`
  ]
  if (!record.formula) {
    lines.push('It found no composition of its own for it.')
    return lines.join(' ')
  }
  lines.push(`It read it as ${record.formula}.`)
  const named = batchTierReasons(reasons)
  const hold = persistenceHold(reasons, tier)
  if (hold === 'held') {
    lines.push(
      'Persistence says an ion is there, not which formula it is, so the reading ' +
        'is held at candidate until an isotope line, another channel or a series ' +
        'of the batch sees it.'
    )
  } else if (hold === 'lower') {
    lines.push(
      'Persistence says an ion is there, not which formula it is, so the reading ' +
        'would be held at candidate until an isotope line, another channel or a ' +
        "series of the batch sees it. The samples' vote already puts it lower."
    )
  } else if (named.length) {
    lines.push(`Kept its tier on: ${named.map((reason) => reason.detail).join('; ')}.`)
  }
  return lines.join(' ')
}
