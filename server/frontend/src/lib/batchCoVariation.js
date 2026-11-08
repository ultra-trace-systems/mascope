/**
 * What the batch's co-variation reading says of a batch peak, in words.
 *
 * "Read co-variation" (step 4.2 of the assignment plan, `batch_coherence.py`)
 * reads the trace of every compound the batch sees through two ionization
 * channels against the other channel's: log intensities, divided by each
 * spectrum's reagent signal, with the batch's common mode taken out, and
 * Pearson's r over the spectra both channels hold. The batch peak carries the
 * reading as `co_variation` (the sample's row as `batch_co_variation`): the
 * strongest partner's `verdict`, `r`, `n_spectra` and `reason` at the top and
 * every partner under `partners`. It is display-only: no tier reads it, and
 * these words say so.
 */

export const VERDICT_CO_VARYING = 'co_varying'
export const VERDICT_NOT_CO_VARYING = 'not_co_varying'
export const VERDICT_UNTESTABLE = 'untestable'

/** The fewest spectra the reading needs (`coherence.MIN_SPECTRA`). */
export const MIN_SPECTRA = 10

const rFormat = new Intl.NumberFormat('en', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2
})

const formatR = (r) => rFormat.format(r)

const spectra = (n) => `${n} spectr${n === 1 ? 'um' : 'a'}`

const channelOf = (reading) =>
  reading?.channel ? `its ${reading.channel} channel` : 'its other channel'

/** Why a pair could not be read, as a clause. */
function untestableWhy(reading) {
  if (reading.reason === 'flat') return 'one of the two hardly moves across the batch'
  const n = reading.n_spectra ?? 0
  if (!n) return 'the two are never seen in the same spectrum'
  return `the two are seen together in ${spectra(n)} only, fewer than the ${MIN_SPECTRA} the reading needs`
}

/** One partner's reading, as a sentence naming its channel. */
function sentence(reading, channel) {
  if (reading.verdict === VERDICT_CO_VARYING)
    return `Co-varies with ${channel} across the batch: r ${formatR(reading.r)} over ${spectra(reading.n_spectra)}.`
  if (reading.verdict === VERDICT_NOT_CO_VARYING)
    return `Does not co-vary with ${channel} across the batch: r ${formatR(reading.r)} over ${spectra(reading.n_spectra)}.`
  return `Its co-variation with ${channel} cannot be read: ${untestableWhy(reading)}.`
}

/**
 * The ledger cell's text: the strongest partner's r, or that it could not be
 * read. Empty for no record.
 */
export function coVariationLabel(record) {
  if (!record) return ''
  if (record.verdict === VERDICT_UNTESTABLE) return 'untestable'
  return `r ${formatR(record.r)}`
}

/**
 * What the ledger's column sorts on: the strongest partner's r, null where it
 * was not read (an untestable pair records none), so those sort last in both
 * directions.
 */
export function coVariationRank(record) {
  return record?.r ?? null
}

/** The inspector's one line: the strongest partner's verdict and channel. */
export function coVariationSummary(record) {
  if (!record) return ''
  const channel = record.partner_channel ?? 'another channel'
  if (record.verdict === VERDICT_CO_VARYING) return `Co-varies with ${channel} across the batch`
  if (record.verdict === VERDICT_NOT_CO_VARYING)
    return `Does not co-vary with ${channel} across the batch`
  return `Co-variation with ${channel} cannot be read`
}

/**
 * The full account: one sentence per partner channel, strongest first, then
 * what the traces were read through, and that no tier reads it.
 */
export function coVariationTooltip(record) {
  if (!record) return ''
  const partners = record.partners?.length
    ? record.partners
    : [{ ...record, channel: record.partner_channel }]
  const lines = partners.map((reading) => sentence(reading, channelOf(reading)))
  const read =
    record.normalised === false
      ? "Log intensities with the batch's common mode taken out; the batch peaks hold no reagent ion to normalise by."
      : "Log intensities, divided by each spectrum's reagent signal, with the batch's common mode taken out."
  return [...lines, read, 'Shown for reading only: no tier moves on it.'].join(' ')
}
