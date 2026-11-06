// The lines of a monoisotopic row's predicted isotope envelope that its run
// asked the spectrum for, as the tiering pass records them in
// `provenance.predicted_envelope` (peak_assignments/tiering.py, step 3.4e of
// the assignment quality plan): every line the detectability gate says the
// spectrum should show, its predicted m/z and its share of the row's own line,
// and what the spectrum held there - a peak (`found`), nothing (`absent`, which
// holds the row at candidate at most), or nothing it could be read on
// (`untestable`: only an artifact's peak in its window, or beyond the first or
// last peak, which the run takes as the ends of what the spectrum measured).
//
// The inspector lists the lines that were not found under the found ones, and
// the spectrum chart draws them as empty circles. Kept apart from both so the
// two read one record one way.

export const LINE_FOUND = 'found'
export const LINE_ABSENT = 'absent'
export const LINE_UNTESTABLE = 'untestable'

// A share of the row's own line: 10%, 2.5%.
const shareFormat = new Intl.NumberFormat('en-US', {
  style: 'percent',
  maximumFractionDigits: 1
})

/**
 * The lines a row's run asked the spectrum for, in the order it asked them.
 *
 * A row with no record - a run from before the rule, one that read no
 * spectrum, an imported run, an isotopologue - has none, and neither has an
 * entry missing its predicted m/z or share.
 *
 * @param {object|null} provenance - a monoisotopic row's provenance, from the
 *   detail fetch
 * @returns {Array<object>}
 */
export function predictedLines(provenance) {
  const lines = provenance?.predicted_envelope?.lines
  if (!Array.isArray(lines)) return []
  return lines.filter(
    (line) =>
      line &&
      typeof line === 'object' &&
      Number.isFinite(line.predicted_mz) &&
      Number.isFinite(line.predicted_share)
  )
}

/**
 * The asked lines the spectrum did not hold: absent and untestable ones.
 *
 * @param {object|null} provenance
 * @returns {Array<object>}
 */
export function notFoundLines(provenance) {
  return predictedLines(provenance).filter(
    (line) => line.state === LINE_ABSENT || line.state === LINE_UNTESTABLE
  )
}

/**
 * The words a not-found line is listed with.
 *
 * @param {object} line - one entry of `notFoundLines`
 * @returns {string}
 */
export function notFoundWord(line) {
  return line?.state === LINE_UNTESTABLE ? 'not tested' : 'not found'
}

/**
 * Why the run asked a line: a TOF's M+2 rule, the line's expected
 * signal-to-noise, or on a peak the file records none for, its share alone.
 */
function askedBecause(line, asksShare) {
  if (line?.tof_m2) return 'On a TOF a bromine or chlorine reading must show its own M+2 line.'
  if (line?.expected_snr != null) {
    return "The run's detectability gate says the spectrum should show it at this peak's signal-to-noise."
  }
  return Number.isFinite(asksShare)
    ? `The peak has no signal-to-noise, so the run's detectability gate asks for every line from ${shareFormat.format(asksShare)} of the row's own line.`
    : "The peak has no signal-to-noise, so the run's detectability gate asks for lines by their share of the row's own line."
}

/**
 * A not-found line's hover text: what the spectrum held there, and what that
 * did to the row.
 *
 * @param {object} line - one entry of `notFoundLines`
 * @param {object|null} [provenance] - the row's provenance, whose record says
 *   the share the gate asked lines from where the peak has no signal-to-noise
 * @returns {string}
 */
export function notFoundTooltip(line, provenance = null) {
  if (line?.state === LINE_UNTESTABLE) {
    return line.untestable === 'out_of_range'
      ? 'Not tested: the line falls beyond the first or last peak of the spectrum, which the run takes as the ends of what it measured.'
      : "Not tested: only an artifact's peak sits where the line should be, which neither confirms nor refutes it."
  }
  const why = askedBecause(line, provenance?.predicted_envelope?.asks_share)
  return `Not found: no peak sits within the run's match window of this predicted line. ${why} It holds the row at candidate at most.`
}
