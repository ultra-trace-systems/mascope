/**
 * What the batch ledger says of a batch peak whose trace holds a constant
 * ratio to the batch's normaliser: a flat series, an origin flag.
 *
 * "Read co-variation" also reads each committed reading's trace alone (step
 * 4.2e of the assignment plan, `batch_coherence.py`): divided by its
 * partition's normaliser - the reagent ions' summed signal, else each
 * sample's total ion current - its coefficient of variation over the samples
 * it is in. A trace under 0.25, in half the partition's samples or more, in a
 * partition that varies, is flagged as background: the peak's ion comes from
 * the source or the inlet rather than following what was sampled. The batch
 * peak carries the reading as `flat_series` (a sample's row as
 * `batch_flat_series`): the `verdict` (`flat`, `varying`, or `untestable`
 * with its `reason`), the `cv`, `n_present` of `n_spectra` and their `share`,
 * the `normaliser`, and on a flat reading the ion of the batch's own source
 * list its ion is (`source_ion`), where one is. The flag says where an ion
 * comes from, not whether its formula is right: it moves no tier.
 */

/** The verdict the flag marks. */
export const FLAT = 'flat'

const NORMALISERS = Object.freeze({
  reagent: "the batch's reagent ions",
  tic: "the samples' total ion current"
})

const UNTESTABLE = Object.freeze({
  few_spectra: 'it is in too few samples to read',
  rare: "it is in under half the batch's samples, so its flatness says nothing",
  steady_batch: 'the batch holds still, so a flat trace tells a background from nothing'
})

const percent = (share) =>
  typeof share === 'number' && Number.isFinite(share) ? `${Math.round(share * 100)} %` : '?'

const number = (value) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '?'

/**
 * A batch peak's flat-series reading where it reads the trace flat; null
 * otherwise. What the ledger marks and the filter keeps.
 *
 * @param {object} row - a batch peak as the ledger serves it
 * @returns {object|null}
 */
export function flatSeriesOf(row) {
  const record = row?.flat_series
  return record && record.verdict === FLAT ? record : null
}

/**
 * Whether a flat reading's ion is one the batch's own source list names:
 * `source` where it is, '' otherwise.
 *
 * @param {object|null} record - `flat_series`, or a row's `batch_flat_series`
 * @returns {'source'|''}
 */
export function flatSeriesKind(record) {
  return record?.verdict === FLAT && record.source_ion ? 'source' : ''
}

/**
 * The one line the inspector and the mark's label read.
 *
 * @param {object|null} record - `flat_series`, or a row's `batch_flat_series`
 * @returns {string} '' for no record
 */
export function flatSeriesSummary(record) {
  if (!record) return ''
  if (record.verdict === FLAT) {
    const line = `Flat series: cv ${number(record.cv)} in ${percent(record.share)} of the samples`
    return record.source_ion ? `${line}, a ${record.source_ion.label} ion of the source` : line
  }
  if (record.verdict === 'varying') return `Varies across the batch: cv ${number(record.cv)}`
  return 'Flatness not read'
}

/**
 * The full account: what the trace was read against, what it found, and that
 * the tier stands.
 *
 * @param {object|null} record - `flat_series`, or a row's `batch_flat_series`
 * @returns {string} '' for no record
 */
export function flatSeriesTooltip(record) {
  if (!record) return ''
  let against
  if (record.normalised) {
    against = NORMALISERS[record.normaliser] ?? record.normaliser
  } else if (record.normaliser_rejected) {
    against = 'nothing: the normaliser moves too much to divide by'
  } else {
    against = 'nothing: the batch holds neither reagent ions nor a total ion current'
  }
  const lines = [
    `Its trace was read against ${against}, over ${record.n_present ?? '?'} of the ` +
      `batch's ${record.n_spectra ?? '?'} samples (${percent(record.share)}).`
  ]
  if (record.verdict === 'varying') {
    lines.push(`Its ratio varies, a cv of ${number(record.cv)}: it follows what was sampled.`)
    return lines.join(' ')
  }
  if (record.verdict !== FLAT) {
    lines.push(`Not read: ${UNTESTABLE[record.reason] ?? record.reason ?? 'unknown'}.`)
    return lines.join(' ')
  }
  lines.push(
    `Its ratio holds still, a cv of ${number(record.cv)}: likely a background of the ` +
      'source or the inlet rather than what was sampled.'
  )
  if (record.source_ion) {
    lines.push(`The source's list names its ion, ${record.source_ion.label}.`)
  }
  lines.push(
    'This says where the ion comes from, not whether the formula is right: the tier stands.'
  )
  return lines.join(' ')
}
