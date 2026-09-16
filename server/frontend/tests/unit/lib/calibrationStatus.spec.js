import { describe, it, expect } from 'vitest'

import { calibrationStatus } from '@/lib/calibrationStatus'

describe('calibrationStatus', () => {
  it('shows an explicit not-calibrated badge when calibration was never attempted', () => {
    for (const value of [null, undefined]) {
      const status = calibrationStatus(value)
      expect(status.state).toBe('none')
      expect(status.clickable).toBe(false)
      // Fixed product copy, pinned exactly.
      expect(status.tooltip).toBe(
        'No calibration collection defined for the ionization mode, ' +
          'or no matching peaks found.'
      )
    }
  })

  it('flags a failed calibration with attempts and error in the tooltip', () => {
    const status = calibrationStatus({
      status: 'failed',
      verified: false,
      error: 'No calibration peaks found',
      attempts: 7,
      mz_error_tolerance: 320
    })

    expect(status.state).toBe('failed')
    expect(status.severity).toBe('danger')
    expect(status.tooltip).toContain('after 7 attempts')
    expect(status.tooltip).toContain('No calibration peaks found')
    expect(status.tooltip).toContain('match computation is skipped')
  })

  it('reports an applied fit with its quality details', () => {
    const status = calibrationStatus({
      status: 'ok',
      verified: true,
      mode: 'one-point',
      par: { calibration_factor: 1.0000125 },
      quality: {
        n_points: 6,
        pre_fit_mz_error_ppm: 12.53,
        post_fit_mz_error_ppm: 0.42
      }
    })

    expect(status.state).toBe('ok')
    expect(status.clickable).toBe(true)
    expect(status.tooltip).toContain('6 points')
    expect(status.tooltip).toContain('12.53 ppm')
    expect(status.tooltip).toContain('0.42 ppm')
  })

  it('reports an applied fit without quality as plainly calibrated', () => {
    const status = calibrationStatus({ mode: 'one-point', verified: true })

    expect(status.state).toBe('ok')
    expect(status.tooltip).toBe('m/z calibrated')
  })

  it('treats an unverified record as needing attention', () => {
    const status = calibrationStatus({ mode: 'one-point', verified: false })

    expect(status.state).toBe('unverified')
    expect(status.tooltip).toContain('not verified')
  })

  it('uses singular wording for a single point and attempt', () => {
    expect(calibrationStatus({ status: 'failed', attempts: 1 }).tooltip).toContain(
      'after 1 attempt.'
    )
    expect(calibrationStatus({ verified: true, quality: { n_points: 1 } }).tooltip).toContain(
      '1 point'
    )
  })

  it('flags acquisition drift on a calibrated sample', () => {
    const status = calibrationStatus({
      status: 'ok',
      verified: true,
      acquisition_drift: true,
      acquisition_drift_ppm: 12.47,
      quality: { n_points: 6, pre_fit_mz_error_ppm: 12.47, post_fit_mz_error_ppm: 0.35 }
    })

    // The calibration fixed it: informational, not the warning a bad
    // calibration gets.
    expect(status.state).toBe('drifted')
    expect(status.severity).toBe('info')
    expect(status.tooltip).toContain('Acquisition drift 12.47 ppm')
    expect(status.tooltip).toContain('retuning')
    expect(status.tooltip).toContain('corrected')
  })

  it('shows the original drift after a re-calibration on the corrected axis', () => {
    const status = calibrationStatus({
      status: 'ok',
      verified: true,
      acquisition_drift: true,
      acquisition_drift_ppm: 12.6,
      quality: { n_points: 4, pre_fit_mz_error_ppm: 0.35, post_fit_mz_error_ppm: 0.3 }
    })

    expect(status.state).toBe('drifted')
    expect(status.tooltip).toContain('Acquisition drift 12.60 ppm')
  })

  const BELOW_BAR = {
    status: 'poor',
    verified: false,
    quality_issues: [
      { code: 'residual', message: 'Mean m/z error after calibration is 1.26 ppm (limit 1 ppm).' },
      {
        code: 'signal',
        message: 'Calibrants carry 0.005% of the total ion current (at least 0.01% needed).'
      }
    ],
    quality: { n_points: 2, pre_fit_mz_error_ppm: 1.26, post_fit_mz_error_ppm: 1.26 }
  }

  it('warns about a fit below the quality bar, with its reasons', () => {
    const status = calibrationStatus(BELOW_BAR)

    expect(status.state).toBe('poor')
    expect(status.severity).toBe('warn')
    expect(status.clickable).toBe(true)
    expect(status.tooltip).toContain('below the quality bar')
    expect(status.tooltip).toContain('1.26 ppm (limit 1 ppm)')
    expect(status.tooltip).toContain('of the total ion current')
    expect(status.tooltip).toContain('skipped')
  })

  it('tells a poor fit from a drifted one when both apply', () => {
    const status = calibrationStatus({
      ...BELOW_BAR,
      acquisition_drift: true,
      acquisition_drift_ppm: 14.2
    })

    expect(status.state).toBe('poor')
    expect(status.severity).toBe('warn')
    expect(status.tooltip).toContain('Acquisition drift 14.20 ppm')
  })

  it('warns about a fit below the bar that a warn-only gate let through', () => {
    const status = calibrationStatus({ ...BELOW_BAR, verified: true, quality_gate: 'warn' })

    expect(status.state).toBe('warned')
    expect(status.severity).toBe('warn')
    expect(status.tooltip).toContain('below the quality bar')
    expect(status.tooltip).toContain('Matches and assignments use it')
    expect(status.tooltip).not.toContain('skipped')
    expect(status.tooltip).not.toContain('Accepted')
  })

  it('keeps warning about an accepted fit below the bar', () => {
    const status = calibrationStatus({ ...BELOW_BAR, verified: true, accepted_by: 7 })

    expect(status.state).toBe('accepted')
    expect(status.severity).toBe('warn')
    expect(status.tooltip).toContain('Accepted by an operator')
    expect(status.tooltip).not.toContain('skipped')
  })

  it('shows a TOF file on its acquisition axis as not calibrated', () => {
    for (const record of [
      { mode: 0, par: [1, 2], status: 'unfitted', verified: false },
      // Registered before the status was stamped.
      { mode: 0, par: [1, 2] }
    ]) {
      const status = calibrationStatus(record)
      expect(status.state).toBe('unfitted')
      expect(status.clickable).toBe(true)
      expect(status.tooltip).toContain('Not calibrated')
      expect(status.tooltip).toContain('skipped')
    }
  })

  it('keeps plain calibrated state without the drift flag', () => {
    const status = calibrationStatus({
      status: 'ok',
      verified: true,
      quality: { n_points: 6, pre_fit_mz_error_ppm: 12.47 }
    })

    expect(status.state).toBe('ok')
  })
})
