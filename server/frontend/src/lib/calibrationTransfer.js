/**
 * A TOF file's m/z calibration transferred from its neighbours.
 *
 * A file whose ionization mode has no calibration collection, on an
 * instrument that alternates it with fitted modes, takes the calibration of
 * the nearest fitted files of the same instrument and polarity: the two on
 * either side interpolated in time, or the one within the window. Its record
 * says `status: "transferred"` and keeps under `transfer` each source's
 * `sample_file_id`, its signed time gap `gap_s` and its `weight`, the gap to
 * the nearest source (`gap_s`) and the window (`window_s`), both in seconds.
 * A fit of the file itself replaces it.
 */

const minutes = (seconds) => {
  const value = Math.round(seconds / 60)
  return `${value} min`
}

/** Whether a record is a transfer from neighbouring files. */
export const isTransferred = (mzCalibration) => mzCalibration?.status === 'transferred'

/**
 * The sentence that says where a transferred calibration came from, or null
 * for any other record.
 *
 * @param {object|null|undefined} mzCalibration - `sample.mz_calibration`
 * @returns {string|null}
 */
export function transferText(mzCalibration) {
  if (!isTransferred(mzCalibration)) return null
  const transfer = mzCalibration.transfer ?? {}
  const sources = transfer.sources ?? []
  const gap = typeof transfer.gap_s === 'number' ? minutes(transfer.gap_s) : null
  if (sources.length === 2) {
    return (
      'm/z calibration transferred from the two nearest fitted files of the ' +
      `instrument, interpolated in time${gap ? ` (the nearer ${gap} away)` : ''}.`
    )
  }
  return (
    'm/z calibration transferred from the nearest fitted file of the instrument' +
    `${gap ? `, ${gap} away` : ''}.`
  )
}
