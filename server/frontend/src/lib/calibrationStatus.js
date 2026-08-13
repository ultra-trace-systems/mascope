/**
 * Presentation model for a sample's m/z calibration outcome.
 *
 * The backend persists the outcome in `sample_file.mz_calibration`:
 * - `null` - calibration was never attempted (blank file, or the ionization
 *   mode has no calibration collection); shown as a muted "not calibrated"
 *   badge so the column reads as an explicit state, not a missing value.
 * - `{status: "failed", ...}` - the automatic pipeline gave up; the sample is
 *   uncalibrated and its matches are skipped until it is recalibrated.
 * - `{status: "ok"/verified: true, ...}` - an applied fit, optionally with a
 *   `quality` block (calibration point count, pre/post mean |m/z error| in
 *   ppm) recorded at fit time.
 */

const ppm = (value) => (value === null || value === undefined ? null : `${value.toFixed(2)} ppm`)

/**
 * Derive the calibration badge for a sample row.
 *
 * @param {object|null|undefined} mzCalibration - `sample.mz_calibration` record
 * @returns {{state: string, icon: string, severity: string, tooltip: string}|null}
 *   Badge descriptor, or null when there is nothing to show.
 */
export function calibrationStatus(mzCalibration) {
  if (!mzCalibration) {
    return {
      state: 'none',
      icon: 'ph ph-scales',
      severity: 'secondary',
      tooltip:
        'Not calibrated: no m/z calibration was applied (the ionization mode ' +
        'has no calibration collection, or the file is a blank). ' +
        'Click to calibrate manually.'
    }
  }

  if (mzCalibration.status === 'failed') {
    const attempts = mzCalibration.attempts
      ? ` after ${mzCalibration.attempts} attempt${mzCalibration.attempts === 1 ? '' : 's'}`
      : ''
    const error = mzCalibration.error ? ` (${mzCalibration.error})` : ''
    return {
      state: 'failed',
      icon: 'ph ph-scales',
      severity: 'warn',
      tooltip:
        `m/z calibration failed${attempts}${error}. ` +
        'The sample is uncalibrated and match computation is skipped. ' +
        'Click to calibrate manually.'
    }
  }

  const quality = mzCalibration.quality
  const detail = quality
    ? [
        `${quality.n_points} calibration point${quality.n_points === 1 ? '' : 's'}`,
        quality.pre_fit_mz_error_ppm != null
          ? `mean |m/z error| ${ppm(quality.pre_fit_mz_error_ppm)} before, ${
              ppm(quality.post_fit_mz_error_ppm) ?? '?'
            } after`
          : null
      ]
        .filter(Boolean)
        .join(', ')
    : null

  if (!mzCalibration.verified) {
    return {
      state: 'unverified',
      icon: 'ph ph-scales',
      severity: 'secondary',
      tooltip:
        'm/z calibration is not verified. Click to calibrate manually.' +
        (detail ? ` (${detail})` : '')
    }
  }

  return {
    state: 'ok',
    icon: 'ph ph-scales',
    severity: 'muted',
    tooltip: `m/z calibrated${detail ? `: ${detail}` : ''}`
  }
}
