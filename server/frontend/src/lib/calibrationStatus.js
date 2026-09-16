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
 * - `{status: "unfitted", ...}` - a TOF file still on the m/z axis its
 *   acquisition wrote (the converter's coefficients); never fitted, so its
 *   matches are skipped too. Records registered before the status was stamped
 *   carry neither `status` nor `verified`.
 * - `{status: "poor", quality_issues: [...]}` - a fit was applied but misses
 *   the quality bar. Unverified (matches skipped) unless an operator accepted
 *   it (`verified: true`, `accepted_by`), in which case downstream results run
 *   on it and the badge keeps saying so.
 * - `{status: "ok", verified: true, ...}` - an applied fit that clears the
 *   bar, optionally with a `quality` block (calibration point count, pre/post
 *   mean |m/z error| in ppm) recorded at fit time. `acquisition_drift` marks a
 *   file whose own axis was far off before the fit corrected it: the
 *   calibration is fine, the instrument wants retuning.
 */

const ppm = (value) => (value === null || value === undefined ? null : `${value.toFixed(2)} ppm`)

const issueText = (mzCalibration) =>
  (mzCalibration.quality_issues ?? []).map((issue) => issue.message).join(' ')

const driftText = (mzCalibration) => {
  const drift = ppm(mzCalibration.acquisition_drift_ppm)
  return `Acquisition drift${drift ? ` ${drift}` : ''} – consider retuning the instrument.`
}

/**
 * Derive the calibration badge for a sample row.
 *
 * States and their severities, from most to least urgent: `failed` (danger),
 * `poor` and `accepted` (warn - a calibration that is not good enough, used or
 * not), `drifted` (info - calibrated fine, the instrument needs attention),
 * `unfitted`/`unverified` (secondary), `ok` (muted), `none`.
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
      severity: 'danger',
      clickable: true,
      tooltip:
        `m/z calibration failed${attempts}${error}. ` +
        'The sample is uncalibrated and match computation is skipped. ' +
        'Click to calibrate manually.'
    }
  }

  // Same test as the backend's `is_unfitted_record`.
  const unfitted =
    mzCalibration.status === 'unfitted' ||
    (mzCalibration.status === undefined &&
      mzCalibration.verified === undefined &&
      mzCalibration.quality === undefined &&
      Number.isInteger(mzCalibration.mode) &&
      mzCalibration.par !== undefined)
  if (unfitted) {
    return {
      state: 'unfitted',
      icon: 'ph ph-scales',
      severity: 'secondary',
      clickable: true,
      tooltip:
        'Not calibrated: the m/z axis is the one the acquisition wrote. ' +
        'Match computation and peak assignment are skipped. Click to calibrate.'
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
  const drift = mzCalibration.acquisition_drift ? ` ${driftText(mzCalibration)}` : ''

  if (mzCalibration.status === 'poor') {
    const issues = issueText(mzCalibration)
    if (mzCalibration.verified) {
      return {
        state: 'accepted',
        icon: 'ph ph-scales',
        severity: 'warn',
        clickable: true,
        tooltip:
          `m/z calibration below the quality bar${detail ? ` (${detail})` : ''}: ${issues} ` +
          'Accepted by an operator, so matches and assignments use it – ' +
          `treat their mass errors with care.${drift}`
      }
    }
    return {
      state: 'poor',
      icon: 'ph ph-scales',
      severity: 'warn',
      clickable: true,
      tooltip:
        `m/z calibration below the quality bar${detail ? ` (${detail})` : ''}: ${issues} ` +
        'Match computation and peak assignment are skipped. ' +
        `Click to recalibrate, or to accept the fit.${drift}`
    }
  }

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
    return {
      state: 'drifted',
      icon: 'ph ph-scales',
      severity: 'info',
      clickable: true,
      tooltip:
        `m/z calibrated${detail ? ` (${detail})` : ''}; the calibration corrected the ` +
        `instrument's offset.${drift}`
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
