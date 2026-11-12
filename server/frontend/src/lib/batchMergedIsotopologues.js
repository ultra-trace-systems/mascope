/**
 * What the batch's merged-isotopologue reading says of a batch peak, in words.
 *
 * "Strip isotopologue rows" (step 4.2f of the assignment plan,
 * `batch_merged_isotopologues.py`) reads every batch peak the batch commits a
 * formula to against the 13C, 15N, 18O, 34S, Si, 37Cl and 81Br lines of the
 * brighter ones below it, and strips a row whose height holds a line's share
 * across the batch to that compound's isotopologue: its samples' rows are
 * rewritten as the parent's isotope line, so it folds under the parent as any
 * isotopologue does. A stripped row carries what it was read as under
 * `merged_isotopologue_of`, with what its samples' runs had read it as
 * (`displaced`); the parent carries the rows it took under
 * `merged_isotopologues`.
 */

const percent = new Intl.NumberFormat('en', { style: 'percent', maximumFractionDigits: 1 })
const factor = new Intl.NumberFormat('en', { maximumFractionDigits: 2 })

const samples = (n) => `${n} sample${n === 1 ? '' : 's'}`

/** The batch peak a stripped row is the isotopologue of, or null. */
export function strippedParentId(row) {
  return row?.merged_isotopologue_of?.batch_peak_id ?? null
}

/** The isotope line a stripped row was read as, or ''. */
export function strippedLabel(row) {
  return row?.merged_isotopologue_of?.label ?? ''
}

/** What the samples' runs had read a stripped row as, as a phrase. */
function displacedPhrase(record) {
  const formula = record?.displaced?.formula
  return formula ? `as ${formula}` : 'as a compound of its own'
}

/** How many of a reading's samples read above twice the line's share. */
function aboveClause(record) {
  const above = record.above ?? 0
  return above ? `${above} of them above twice it` : 'none above twice it'
}

/** The stripped row's account: whose line, read how, and what it was. */
export function strippedTooltip(record) {
  if (!record) return ''
  const share = record.ratio != null ? ` (${percent.format(record.ratio)} of its height)` : ''
  return [
    `Stripped from the ledger as the ${record.label} isotopologue of`,
    `${record.formula ?? 'its main peak'}${share}: the samples' runs read this peak`,
    `${displacedPhrase(record)}, and across the batch its height was`,
    `${factor.format(record.median_factor ?? 0)} times the line's share`,
    `(the median over ${samples(record.spectra ?? 0)}, ${aboveClause(record)}).`,
    `Its samples were rewritten as that peak's isotope line.`
  ].join(' ')
}

/** How many rows a parent took as its isotope lines. */
export function strippedLinesCount(record) {
  return record?.n_lines ?? record?.lines?.length ?? 0
}

/** The parent's mark: how many rows it took as its lines. */
export function strippedLinesSummary(record) {
  const n = strippedLinesCount(record)
  return `${n} row${n === 1 ? '' : 's'} stripped to this compound's isotope lines`
}

/** One line in the parent's account. */
function lineEntry(entry) {
  const was = entry.displaced_formula ? `read as ${entry.displaced_formula}` : 'read on its own'
  return (
    `${entry.label}: ${was}, at ${factor.format(entry.median_factor ?? 0)} times ` +
    `its share over ${samples(entry.spectra ?? 0)}`
  )
}

/** The parent's account: each row it took, and what that row had been. */
export function strippedLinesTooltip(record) {
  if (!record) return ''
  return [
    `${strippedLinesSummary(record)}:`,
    ...(record.lines ?? []).map((entry) => `${lineEntry(entry)}.`),
    'Unfold the isotopologues to see them under it.'
  ].join(' ')
}
