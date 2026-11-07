/**
 * What the batch ledger says of a homologous series its "Detect series" run
 * found for a row's formula.
 *
 * The record is the one the server keeps on the batch peak
 * (`batch_peaks.SERIES_KEY`): the unit the series steps by, which way and how
 * many steps from the assigned batch peak its chain starts at, that peak's
 * formula, and every unit whose series named the same formula. Worded as the
 * tiering pass words the same record on a sample's row, so the two read alike.
 */

const COUNT_WORDS = { 1: 'one', 2: 'two', 3: 'three' }

/**
 * The tooltip of a row's series mark.
 *
 * @param {object|null} series The row's `series` record, or null.
 * @returns {string} A sentence, or '' for no series.
 */
export function seriesTooltip(series) {
  if (!series) return ''
  const unit = series.unit || 'repeat unit'
  const others = (series.units ?? []).filter((other) => other !== series.unit)
  const also = others.length ? ` It is in the ${others.join(' and ')} series too.` : ''
  const depth = Number.isInteger(series.depth) && series.depth > 0 ? series.depth : null
  if (!series.root_formula || depth === null) {
    return `In the batch's ${unit} series.${also}`
  }
  const steps = COUNT_WORDS[depth] ?? String(depth)
  const direction = series.step === -1 ? 'lighter' : 'heavier'
  return (
    `In the batch's ${unit} series: ${steps} ${unit} ${direction} than ` +
    `${series.root_formula}, which the batch assigns.${also}`
  )
}
