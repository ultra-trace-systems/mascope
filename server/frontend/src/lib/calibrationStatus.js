/**
 * Presentation model for a sample's m/z calibration outcome.
 *
 * The backend persists the outcome in `sample_file.mz_calibration`:
 * - `null` - calibration was never attempted (blank file, or the ionization
 *   mode has no calibration collection); shown as a muted "not calibrated"
 *   badge so the column reads as an explicit state, not a missing value.
 * - `{status: "failed", ...}` - the automatic pipeline gave up; the sample is
 *   uncalibrated and its matches are skipped until it is recalibrated.
 * - `{status: "skipped", verified: true, reason, skipped_by, skipped_utc}` - an
 *   operator declared the file deliberately uncalibrated. Distinct from `null`
 *   in every way that matters: it is explicit, attributed and explained, and
 *   `verified: true` keeps matching running exactly as it does for a file with
 *   no record at all.
 * - `{status: "ok"/verified: true, ...}` - an applied fit, optionally with a
 *   `quality` block (calibration point count, pre/post mean |m/z error| in
 *   ppm) recorded at fit time.
 */

const ppm = (value) => (value === null || value === undefined ? null : `${value.toFixed(2)} ppm`)

/** The local date of an ISO timestamp, or null when it is missing or unparsable. */
const day = (value) => {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleDateString()
}

/**
 * Whether `POST /calibration/mz_skip` would accept this record.
 *
 * Mirrors `_is_applied_fit` in `calibration_controller.py`, so the UI offers
 * the action exactly where the backend takes it. A file whose m/z axis carries
 * a fit Mascope applied cannot be called uncalibrated; anything else can:
 * no record at all, a given-up automatic attempt, an existing marker being
 * relabelled, and - the case a status check alone gets wrong - the
 * instrument's own acquisition calibration, which every converted Tofwerk h5
 * file carries as a statusless `{mode, par}` record from the moment it lands.
 * Applied fits predating the `status` discriminator carry `verified`, which is
 * what tells those two statusless shapes apart.
 *
 * @param {object|null|undefined} mzCalibration - `sample.mz_calibration` record
 * @returns {boolean} True when the file may be marked calibration-skipped.
 */
export function canSkipCalibration(mzCalibration) {
  if (!mzCalibration) return true
  const status = mzCalibration.status
  if (status !== undefined && status !== null) return status === 'failed' || status === 'skipped'
  return !('verified' in mzCalibration)
}

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
      // Clickable since the dialog gained the skip action: a never-attempted
      // file is its archetypal target, so this badge is the entry point the
      // tooltip points at. Without it the only route in is the sample context
      // menu, which names neither calibration skipping nor this state.
      clickable: true,
      tooltip:
        'Not calibrated: calibration was never attempted - the ionization ' +
        'mode has no calibration collection, or the file is a blank. Click to ' +
        'calibrate, or to record that the file is deliberately left ' +
        'uncalibrated.'
    }
  }

  if (mzCalibration.status === 'skipped') {
    const reason = mzCalibration.reason ? `: ${mzCalibration.reason}` : ''
    const by = mzCalibration.skipped_by ? ` by ${mzCalibration.skipped_by}` : ''
    const on = day(mzCalibration.skipped_utc)
    const attribution = by || on ? `Marked${by}${on ? ` on ${on}` : ''}. ` : ''
    return {
      state: 'skipped',
      // The one state that does not read as a scale: neutral grey alone would
      // be indistinguishable from "unverified" in the column.
      icon: 'ph ph-prohibit',
      severity: 'secondary',
      clickable: true,
      tooltip:
        `Calibration skipped${reason}. ${attribution}` +
        'Matching is unaffected. Click to calibrate or clear the marker.'
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
