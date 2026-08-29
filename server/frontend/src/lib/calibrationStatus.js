/**
 * Presentation model for a sample's m/z calibration outcome.
 *
 * The backend persists the outcome in `sample_file.mz_calibration`:
 * - `null` - calibration was never attempted (blank file, or the ionization
 *   mode has no calibration collection); shown as a muted badge so the column
 *   reads as an explicit state, not a missing value. Its tooltip is fixed
 *   product copy and reads wider than that - an attempt that ran and found
 *   nothing to fit against records a `failed` marker rather than leaving this
 *   NULL.
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
 * @returns {{state: string, icon: string, severity: string, tooltip: string,
 *   clickable: boolean}} Badge descriptor; `clickable` is false when opening
 *   the calibration dialog cannot help (nothing to calibrate against).
 */
export function calibrationStatus(mzCalibration) {
  if (!mzCalibration) {
    return {
      state: 'none',
      icon: 'ph ph-scales',
      severity: 'secondary',
      clickable: false,
      tooltip:
        'No calibration collection defined for the ionization mode, ' +
        'or no matching peaks found.'
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
      clickable: true,
      tooltip:
        `m/z calibration failed${attempts}${error}. ` +
        'The sample is uncalibrated and match computation is skipped. ' +
        'Click to calibrate manually.'
    }
  }

  const quality = mzCalibration.quality
  const detail = quality
    ? [
        `${quality.n_points} point${quality.n_points === 1 ? '' : 's'}`,
        quality.pre_fit_mz_error_ppm != null
          ? `${ppm(quality.pre_fit_mz_error_ppm)} → ${
              ppm(quality.post_fit_mz_error_ppm) ?? '?'
            } mean |m/z error|`
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
      clickable: true,
      tooltip:
        'm/z calibration is not verified. Click to calibrate manually.' +
        (detail ? ` (${detail})` : '')
    }
  }

  if (mzCalibration.acquisition_drift) {
    const drift = ppm(mzCalibration.acquisition_drift_ppm)
    return {
      state: 'drifted',
      icon: 'ph ph-scales',
      severity: 'warn',
      clickable: true,
      tooltip:
        `m/z calibrated${detail ? ` (${detail})` : ''}. ` +
        `Acquisition drift${drift ? ` ${drift}` : ''} – consider retuning the instrument.`
    }
  }

  return {
    state: 'ok',
    icon: 'ph ph-scales',
    severity: 'muted',
    clickable: true,
    tooltip: `m/z calibrated${detail ? `: ${detail}` : ''}`
  }
}
