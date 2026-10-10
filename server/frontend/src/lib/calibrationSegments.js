/**
 * How each scan range of a stitched sample came by its m/z calibration.
 *
 * A file whose method measures one chemistry as several scan ranges is
 * calibrated range by range: two ranges of one file read an ion up to a ppm
 * apart. The backend lists the ranges of the sample's polarity in the fit's
 * quality block, `quality.segments`, both in the fit the calibration dialog
 * previews and in the record a sample carries:
 *
 * - `source: "anchors"` - fitted on calibrants the range holds itself; its
 *   own `quality` block says on how many.
 * - `source: "overlap"` - it has no fit of its own, and takes the
 *   calibration of `origin` shifted by what both ranges read of the
 *   `shared_ions` in their overlap.
 * - `source: "borrowed"` - it has no fit of its own, and takes the
 *   calibration of `origin` as it is.
 *
 * `source` says how a range came by its calibration and not why it has no
 * fit of its own: that is in `note`, where the backend recorded one. A range
 * with no fit need not lack calibrants - the ones it holds may have failed
 * the fit's filters, or disagreed with each other - and a range takes
 * `origin`'s calibration unchanged also where the two share plenty of ions
 * but `origin` was not fitted itself. So the texts state what happened and
 * quote the recorded reason, and guess at neither.
 *
 * A sample that is not stitched has no `segments`, and nothing here says
 * anything about it.
 */

const signedPpm = (value) => `${value < 0 ? '-' : '+'}${Math.abs(value).toFixed(2)} ppm`

// "not fitted on calibrants of its own", with the reason the fit recorded
// where it recorded one: the backend's sentence, as part of this one
const notFitted = (segment) => {
  const note = (segment.note ?? '').trim().replace(/\.$/, '')
  const reason = note ? ` (${note.charAt(0).toLowerCase()}${note.slice(1)})` : ''
  return `not fitted on calibrants of its own${reason}`
}

/**
 * The scan ranges of a fit or of a calibration record.
 *
 * @param {object|null|undefined} mzCalibration - a fit, or `sample.mz_calibration`
 * @returns {object[]} `quality.segments`, empty where there are none
 */
export function calibrationSegments(mzCalibration) {
  return mzCalibration?.quality?.segments ?? []
}

/**
 * One scan range's calibration, as a sentence.
 *
 * The correction is how far the range's factor moves its m/z from what the
 * instrument recorded.
 *
 * @param {object} segment - an entry of `quality.segments`
 * @returns {string}
 */
export function segmentCalibrationText(segment) {
  const correction = signedPpm((segment.calibration_factor - 1) * 1e6)
  if (segment.source === 'overlap') {
    return (
      `${segment.label}: ${notFitted(segment)}; takes the calibration of ${segment.origin} ` +
      `across the ${segment.shared_ions} ions both ranges measure (${correction}).`
    )
  }
  if (segment.source === 'borrowed') {
    return (
      `${segment.label}: ${notFitted(segment)}; takes the calibration of ${segment.origin} ` +
      `unchanged (${correction}).`
    )
  }
  const points = segment.quality?.n_points
  const fitted =
    points == null ? 'its own calibrants' : `${points} calibrant${points === 1 ? '' : 's'}`
  return `${segment.label}: fitted on ${fitted} (${correction}).`
}

/**
 * For the sample's badge: which scan ranges run on another range's calibration.
 *
 * @param {object|null|undefined} mzCalibration - `sample.mz_calibration`
 * @returns {string} A sentence with a leading space, or an empty string when
 *   every range was fitted on its own calibrants or the sample is not stitched.
 *   It does not say why a range has no fit of its own: the calibration dialog
 *   lists each range with the reason recorded for it.
 */
export function carriedSegmentsText(mzCalibration) {
  const carried = calibrationSegments(mzCalibration).filter(
    (segment) => segment.source !== 'anchors'
  )
  if (carried.length === 0) {
    return ''
  }
  const labels = carried.map((segment) => segment.label).join('; ')
  return carried.length === 1
    ? ` Scan range ${labels} was not fitted on calibrants of its own and takes a neighbouring range's calibration.`
    : ` Scan ranges ${labels} were not fitted on calibrants of their own and take a neighbouring range's calibration.`
}
