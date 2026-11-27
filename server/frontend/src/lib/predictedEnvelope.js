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
  return `Not found: no peak sits within the run's match window of this predicted line. ${why} ${askedNumbers(line, provenance)} It holds the row at candidate at most.`
}

/**
 * The gate a row's run asked its lines at, as its record carries it: the
 * peak's signal-to-noise, the expected signal-to-noise a line is asked from,
 * and the share of the row's own line that comes to (`asks_share`). The record
 * lists the lines asked and nothing of the lines under the gate, so the gate is
 * all the card can say of those.
 *
 * @param {object|null} provenance - a monoisotopic row's provenance
 * @returns {{snr: number|null, detectSnr: number|null, asksShare: number}|null}
 *   null where the run asked nothing, or its record does not say at what
 */
export function envelopeGate(provenance) {
  const record = provenance?.predicted_envelope
  if (!record || typeof record !== 'object' || !Array.isArray(record.lines)) return null
  const asksShare = record.asks_share
  if (!Number.isFinite(asksShare) || asksShare <= 0) return null
  const snr = Number.isFinite(record.snr) ? record.snr : null
  return {
    snr,
    detectSnr: snr != null && Number.isFinite(record.detect_snr) ? record.detect_snr : null,
    asksShare
  }
}

const snrFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 })

/**
 * Why the run asked one line, in numbers: its predicted share of M0, its
 * expected signal-to-noise and the signal-to-noise lines are asked from, or on
 * a peak with no signal-to-noise the share lines are asked from.
 *
 * @param {object} line - one entry of `predictedLines`
 * @param {object|null} provenance - the row's provenance
 * @returns {string}
 */
export function askedNumbers(line, provenance) {
  const share = `predicted at ${shareFormat.format(line.predicted_share)} of M0`
  if (line?.tof_m2)
    return `Asked: ${share}, and on a TOF a bromine or chlorine reading must show its own M+2 line.`
  const gate = envelopeGate(provenance)
  if (Number.isFinite(line?.expected_snr) && gate?.detectSnr != null) {
    return `Asked: ${share}, expected signal-to-noise ${snrFormat.format(line.expected_snr)}, asked from ${snrFormat.format(gate.detectSnr)}.`
  }
  if (gate) return `Asked: ${share}, asked from ${shareFormat.format(gate.asksShare)} of M0.`
  return `Asked: ${share}.`
}

/**
 * What the card says of the lines the run did not ask: every line of the
 * pattern predicted under the gate's share of M0, too faint for this peak to
 * show. The record does not list them, so they are named by the gate alone.
 *
 * @param {object|null} provenance - the row's provenance
 * @returns {string|null} null where the run asked nothing
 */
export function notAskedNote(provenance) {
  const gate = envelopeGate(provenance)
  if (!gate) return null
  const under = `Not asked: any line predicted under ${shareFormat.format(gate.asksShare)} of M0.`
  if (gate.snr != null && gate.detectSnr != null) {
    return `${under} At this peak's signal-to-noise of ${snrFormat.format(gate.snr)}, such a line would show under ${snrFormat.format(gate.detectSnr)}, the least the run asks a line to show.`
  }
  return `${under} The file records no signal-to-noise for this peak, so the run asks for lines by their share of M0 alone.`
}

/**
 * The words a pattern with no line beyond M0 is summed up in, where the run
 * asked and found every line under its gate: that is why nothing was asked.
 *
 * @param {object|null} provenance - the row's provenance
 * @returns {string|null} null where the run asked nothing, or asked some line
 */
export function noneAskedSummary(provenance) {
  const gate = envelopeGate(provenance)
  if (!gate || predictedLines(provenance).length) return null
  return `M0 only: every other line is predicted under what this peak can show (under ${shareFormat.format(gate.asksShare)} of M0)`
}

/**
 * Whether the run asked for the line an isotopologue row sits on: a found
 * line of its record on the row's peak, or of the row's label.
 *
 * @param {object} row - an isotopologue row of the family
 * @param {object|null} provenance - the M0's provenance
 * @returns {object|null} the record's line, or null where the run did not ask
 *   for it (or asked nothing)
 */
export function askedLineOf(row, provenance) {
  const found = predictedLines(provenance).filter((line) => line.state === LINE_FOUND)
  return (
    found.find(
      (line) => line.peak_id != null && String(line.peak_id) === String(row?.sample_peak_id)
    ) ??
    found.find((line) => row?.isotope_label && line.line === row.isotope_label) ??
    null
  )
}
