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
 * - `source: "overlap"` - it holds none, and takes the calibration of
 *   `origin` shifted by what both ranges read of the `shared_ions` in their
 *   overlap.
 * - `source: "borrowed"` - it holds none and shares too few ions with a
 *   neighbour, so it takes the calibration of `origin` as it is.
 *
 * A sample that is not stitched has no `segments`, and nothing here says
 * anything about it.
 */

const signedPpm = (value) => `${value < 0 ? '-' : '+'}${Math.abs(value).toFixed(2)} ppm`

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
      `${segment.label}: holds no calibrant, so it takes the calibration of ${segment.origin} ` +
      `across the ${segment.shared_ions} ions both ranges measure (${correction}).`
    )
  }
  if (segment.source === 'borrowed') {
    return (
      `${segment.label}: holds no calibrant and shares too few ions with a neighbouring ` +
      `range, so it takes the calibration of ${segment.origin} as it is (${correction}).`
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
 *   every range was fitted on its own calibrants or the sample is not stitched
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
    ? ` Scan range ${labels} holds no calibrant and takes a neighbouring range's calibration.`
    : ` Scan ranges ${labels} hold no calibrant and take a neighbouring range's calibration.`
}
