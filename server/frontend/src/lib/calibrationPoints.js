/**
 * The points a TOF fit was made on, as the calibration dialog lists them.
 *
 * A TOF fit lists in its quality block, `quality.points`, every calibrant
 * line it chose from: each line the match filters let through, with
 *
 * - `ion`: the ion's formula;
 * - `target_mz`, `observed_mz`: the line's exact m/z, and the m/z of the peak
 *   matched to it on the axis the file carried before the fit;
 * - `residual_ppm`: how far the fit puts the peak from the exact m/z;
 * - `snr`: the peak's signal-to-noise ratio;
 * - `retained`: whether the fit kept the line, or left it out as disagreeing
 *   with the others.
 *
 * The record keeps them, so a line that has started to read off shows there
 * even when the fit left it out. `quality.span` is the exact m/z of the
 * lowest and highest line kept. An Orbitrap fit, and a TOF fit recorded
 * before the points were, list none: the dialog then shows the rows the fit
 * itself sent (`stats`), the lines it kept.
 */

/**
 * The calibrant lines of a fit or record.
 *
 * @param {object|null|undefined} mzCalibration - a fit, or `sample.mz_calibration`
 * @returns {object[]|null} `quality.points`, or null where there are none
 */
export function calibrationPoints(mzCalibration) {
  const points = mzCalibration?.quality?.points
  return Array.isArray(points) ? points : null
}

// A row of the fit's own stats is a line it kept: found again by the exact
// m/z and the peak it was matched to, which are the point's
const rowKey = (mz, observed) => `${mz}|${observed}`

/**
 * The rows of the dialog's calibrant table for a fit that lists its points.
 *
 * One row per point, kept or left out, in the columns the table shows for
 * the lines a fit keeps: the error before the fit and the m/z after it follow
 * from the point. A kept line's share of the TIC is in the fit's own row for
 * it, and a line left out has none. The fit's summary row, its means, comes
 * last.
 *
 * @param {object|null|undefined} fit - the fit previewed (`mzFit.current`)
 * @param {object[]|null|undefined} stats - its rows (`mzFit.stats`): one per
 *   line kept, then the summary
 * @returns {object[]|null} the rows, each with `type` `point` or `summary`;
 *   null for a fit that lists no points
 */
export function calibrationPointRows(fit, stats) {
  const points = calibrationPoints(fit)
  if (points === null) {
    return null
  }
  const shares = new Map(
    (stats ?? [])
      .filter((row) => row.mz != null)
      .map((row) => [rowKey(row.mz, row.sample_peak_mz), row.calibrant_to_tic])
  )
  const rows = points.map((point) => {
    const target = point.target_mz
    const before = point.observed_mz == null ? null : (1e6 * (point.observed_mz - target)) / target
    const after = point.residual_ppm
    return {
      type: 'point',
      ion: point.ion,
      mz: target,
      sample_peak_mz: point.observed_mz,
      match_mz_error: before,
      calibration_mz: after == null ? null : target * (1 + after * 1e-6),
      calibration_mz_error: after,
      mz_error_diff: before == null || after == null ? null : Math.abs(after) - Math.abs(before),
      snr: point.snr,
      calibrant_to_tic: shares.get(rowKey(target, point.observed_mz)) ?? null,
      retained: point.retained
    }
  })
  const summary = (stats ?? []).at(-1)
  return summary && summary.mz === undefined ? [...rows, { ...summary, type: 'summary' }] : rows
}

/**
 * The m/z span a fit's kept lines cover, for the sample's badge.
 *
 * @param {object|null|undefined} mzCalibration - `sample.mz_calibration`
 * @returns {string|null} `m/z 41–228`, or null for a record without a span
 */
export function calibrationSpanText(mzCalibration) {
  const span = mzCalibration?.quality?.span
  if (!Array.isArray(span) || span[0] == null || span[1] == null) {
    return null
  }
  return `m/z ${span[0].toFixed(0)}–${span[1].toFixed(0)}`
}
