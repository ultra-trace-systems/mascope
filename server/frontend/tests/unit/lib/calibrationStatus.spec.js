import { describe, it, expect } from 'vitest'

import { calibrationStatus } from '@/lib/calibrationStatus'

describe('calibrationStatus', () => {
  it('returns null when calibration was never attempted', () => {
    expect(calibrationStatus(null)).toBeNull()
    expect(calibrationStatus(undefined)).toBeNull()
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
    expect(status.tooltip).toContain('6 calibration points')
    expect(status.tooltip).toContain('12.53 ppm before')
    expect(status.tooltip).toContain('0.42 ppm after')
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
      '1 calibration point'
    )
  })
})
