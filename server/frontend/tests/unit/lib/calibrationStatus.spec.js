import { describe, it, expect } from 'vitest'

import { calibrationStatus } from '@/lib/calibrationStatus'

describe('calibrationStatus', () => {
  it('shows an explicit not-calibrated badge when calibration was never attempted', () => {
    for (const value of [null, undefined]) {
      const status = calibrationStatus(value)
      expect(status.state).toBe('none')
      expect(status.clickable).toBe(false)
      expect(status.tooltip).toContain('Not calibrated')
      expect(status.tooltip).toContain('no calibration collection')
      expect(status.tooltip).not.toContain('Click')
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
    expect(status.severity).toBe('warn')
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

    expect(status.state).toBe('drifted')
    expect(status.severity).toBe('warn')
    expect(status.tooltip).toContain('Acquisition drift 12.47 ppm')
    expect(status.tooltip).toContain('retuning')
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

  it('keeps plain calibrated state without the drift flag', () => {
    const status = calibrationStatus({
      status: 'ok',
      verified: true,
      quality: { n_points: 6, pre_fit_mz_error_ppm: 12.47 }
    })

    expect(status.state).toBe('ok')
  })
})
