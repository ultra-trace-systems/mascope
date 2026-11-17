/**
 * What the batch's isotopologue stamping says of a batch peak, in words.
 *
 * "Stamp isotopologues" (step 4.2c of the assignment plan,
 * `batch_isotopologues.py`) predicts the 15N, 18O, 34S, 29Si, 30Si, 37Cl and
 * 81Br lines of every batch peak the batch commits a formula to, finds the
 * batch peak at each one's mass, and stamps a line where its height over its
 * parent's holds the predicted ratio in enough of the samples that can tell. A
 * parent carries what was predicted as `diagnostic_isotopologues`; a stamped
 * line carries its parent as `stamped_isotopologue_of`, with how many of its
 * stamps the stamping predicted and how many the samples' own runs had found
 * (`n_predicted`, `n_picker`). Nothing moves a tier, and these words say so.
 */

export const VERDICT_STAMPED = 'stamped'
export const VERDICT_NOT_STAMPED = 'not_stamped'

const percent = new Intl.NumberFormat('en', { style: 'percent', maximumFractionDigits: 1 })

const samples = (n) => `${n} sample${n === 1 ? '' : 's'}`

/** The batch peak a stamped line folds under in the ledger, or null. */
export function stampParentId(row) {
  return row?.stamped_isotopologue_of?.batch_peak_id ?? null
}

/** The isotope a stamped line is its parent's isotopologue through, or ''. */
export function stampLabel(row) {
  return row?.stamped_isotopologue_of?.label ?? ''
}

/**
 * Whether any of a stamped line's stamps is the stamping's own prediction,
 * rather than a line the samples' runs had already read as the parent's.
 */
export function isPredicted(record) {
  return (record?.n_predicted ?? 0) > 0
}

/** Where a stamped line's stamps came from, as a clause. */
function sources(record) {
  const predicted = record.n_predicted ?? 0
  const picker = record.n_picker ?? 0
  if (!picker) return `predicted by the stamping in all ${samples(predicted)}`
  if (!predicted) return `found by the peak picker in all ${samples(picker)}, and confirmed`
  return `predicted by the stamping in ${samples(predicted)} and found by the peak picker in ${picker}`
}

/** The stamped line's account: whose isotope line, how well, and from where. */
export function stampTooltip(record) {
  if (!record) return ''
  const ratio = record.ratio != null ? ` (${percent.format(record.ratio)} of its height)` : ''
  return [
    `Stamped as the ${record.label} isotopologue of ${record.formula ?? 'its main peak'}${ratio}:`,
    `its height held in ${record.holds} of the ${samples(record.judged)} that could tell`,
    `(${percent.format(record.share ?? 0)}), ${sources(record)}.`,
    'Shown for reading only: no tier moves on it.'
  ].join(' ')
}

/** One predicted isotope line's entry in the parent's account. */
function entryLine(entry) {
  const label = entry.label
  if (entry.stamped)
    return `${label}: stamped, held in ${entry.holds} of ${samples(entry.judged)} (${percent.format(entry.share ?? 0)})`
  if (entry.reason === 'unresolved')
    return `${label}: not separated from another line at this resolution`
  if (entry.reason === 'not_found') return `${label}: predicted, no batch peak at its mass`
  if (entry.reason === 'another_parent')
    return `${label}: held, but the line is another peak's isotopologue`
  if (entry.verdict === VERDICT_NOT_STAMPED)
    return `${label}: found, held in ${entry.holds} of ${samples(entry.judged)} only`
  return `${label}: found, in too few samples that can tell`
}

/** The parent's mark: how many of its predicted isotope lines were stamped. */
export function isotopologuesSummary(record) {
  if (!record) return ''
  const predicted = record.n_predicted ?? record.predicted?.length ?? 0
  return `${record.n_stamped ?? 0} of ${predicted} isotope line${predicted === 1 ? '' : 's'} stamped`
}

/** The parent's account: each isotope line predicted, found and stamped. */
export function isotopologuesTooltip(record) {
  if (!record) return ''
  const lines = (record.predicted ?? []).map(entryLine)
  return [
    `Isotope lines predicted for ${record.formula}: ${isotopologuesSummary(record)}.`,
    ...lines.map((line) => `${line}.`),
    'Shown for reading only: no tier moves on it.'
  ].join(' ')
}

/** Where one sample's stamp came from, in a word for the inspector's line. */
const SAMPLE_SOURCE_WORD = { predicted: 'predicted', picker: 'found by the peak picker' }

/**
 * The inspector's line for a sample's peak the batch stamped: whose isotope
 * line the batch reads it as, and where this sample's stamp came from. The
 * record is the detail read's `batch_stamped_isotopologue_of`.
 */
export function sampleStampSummary(record) {
  if (!record) return ''
  const source = SAMPLE_SOURCE_WORD[record.stamp_source]
  return (
    `Stamped by the batch as the ${record.label} isotopologue of ` +
    `${record.formula ?? 'another batch peak'}${source ? ` (${source})` : ''}`
  )
}

/** The account behind it: this sample's stamp, then the batch's reading. */
export function sampleStampTooltip(record, { linked = false } = {}) {
  if (!record) return ''
  const here =
    record.stamp_source === 'picker'
      ? "In this sample the run's own isotope match had read the peak as its line, and the stamping confirmed it."
      : "In this sample the stamping predicted the line where the run's own match did not read it so."
  return [here, stampTooltip(record), ...(linked ? ['Click to focus its main peak.'] : [])].join(
    ' '
  )
}

/** The inspector's line for the parent's own record: how many lines stamped. */
export function sampleIsotopologuesSummary(record) {
  if (!record) return ''
  return `${isotopologuesSummary(record)} by the batch`
}
