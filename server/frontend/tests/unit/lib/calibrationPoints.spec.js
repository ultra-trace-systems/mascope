import { describe, it, expect } from 'vitest'

import {
  calibrationPointRows,
  calibrationPoints,
  calibrationSpanText
} from '@/lib/calibrationPoints'

// A TOF fit lists every calibrant line it chose from, kept or left out. The
// calibration dialog shows them in the columns it shows the lines a fit keeps,
// so the rest of a row follows from the point, and from the fit's own row for
// a line it kept.

const KEPT = {
  ion: 'NO3-',
  target_mz: 100.0,
  observed_mz: 100.005,
  residual_ppm: -0.5,
  snr: 310.0,
  retained: true
}
const LEFT_OUT = {
  ion: 'H2N3O9-',
  target_mz: 200.0,
  observed_mz: 200.002,
  residual_ppm: 40.0,
  snr: 21.0,
  retained: false
}
const fit = (quality) => ({ mode: 0, par: [1, 2], quality })
const SUMMARY = { match_mz_error: 50.0, calibration_mz_error: 0.5, calibrant_to_tic: 0.07 }

describe('calibrationPoints', () => {
  it('reads the points of a fit or a record, and none where there are none', () => {
    expect(calibrationPoints(fit({ points: [KEPT] }))).toEqual([KEPT])
    expect(calibrationPoints(fit({ n_points: 4 }))).toBe(null)
    expect(calibrationPoints(fit(null))).toBe(null)
    expect(calibrationPoints(null)).toBe(null)
  })
})

describe('calibrationPointRows', () => {
  it('gives every point a row, with what follows from it', () => {
    const rows = calibrationPointRows(fit({ points: [KEPT, LEFT_OUT] }), [
      { mz: 100.0, sample_peak_mz: 100.005, calibrant_to_tic: 0.07 },
      SUMMARY
    ])

    expect(rows.map((row) => row.type)).toEqual(['point', 'point', 'summary'])
    const [kept, leftOut, summary] = rows
    expect(kept).toMatchObject({
      ion: 'NO3-',
      mz: 100.0,
      sample_peak_mz: 100.005,
      calibration_mz_error: -0.5,
      snr: 310.0,
      retained: true,
      calibrant_to_tic: 0.07
    })
    // 5 mDa above at 100 Th was 50 ppm before the fit, and the fit leaves it
    // 0.5 ppm below: 49.5 ppm closer
    expect(kept.match_mz_error).toBeCloseTo(50.0, 6)
    expect(kept.calibration_mz).toBeCloseTo(99.99995, 9)
    expect(kept.mz_error_diff).toBeCloseTo(-49.5, 6)
    // A line left out is in no row of the fit's, so it has no share
    expect(leftOut).toMatchObject({ ion: 'H2N3O9-', retained: false, calibrant_to_tic: null })
    expect(leftOut.match_mz_error).toBeCloseTo(10.0, 6)
    expect(leftOut.mz_error_diff).toBeCloseTo(30.0, 6)
    expect(summary).toEqual({ ...SUMMARY, type: 'summary' })
  })

  it('finds a kept line by its peak as well as its exact m/z', () => {
    // Another reading of the same exact m/z is not this point's row
    const rows = calibrationPointRows(fit({ points: [KEPT] }), [
      { mz: 100.0, sample_peak_mz: 100.009, calibrant_to_tic: 0.5 },
      SUMMARY
    ])

    expect(rows[0].calibrant_to_tic).toBe(null)
  })

  it('leaves out a summary the fit did not send', () => {
    expect(calibrationPointRows(fit({ points: [KEPT] }), null).map((row) => row.type)).toEqual([
      'point'
    ])
    expect(
      calibrationPointRows(fit({ points: [KEPT] }), [{ mz: 100.0, sample_peak_mz: 100.005 }]).map(
        (row) => row.type
      )
    ).toEqual(['point'])
  })

  it('has nothing to say of a fit that lists no points', () => {
    expect(calibrationPointRows(fit({ n_points: 2 }), [SUMMARY])).toBe(null)
  })

  it('leaves what a point does not carry empty, not NaN', () => {
    const [row] = calibrationPointRows(
      fit({ points: [{ ...KEPT, observed_mz: null, residual_ppm: null }] }),
      null
    )

    expect(row.match_mz_error).toBe(null)
    expect(row.calibration_mz).toBe(null)
    expect(row.mz_error_diff).toBe(null)
  })
})

describe('calibrationSpanText', () => {
  it('names the span of the lines kept, in whole Th', () => {
    expect(calibrationSpanText(fit({ span: [41.0386, 228.1747] }))).toBe('m/z 41–228')
  })

  it('says nothing of a record without a span', () => {
    expect(calibrationSpanText(fit({ n_points: 3 }))).toBe(null)
    expect(calibrationSpanText(fit({ span: [null, null] }))).toBe(null)
    expect(calibrationSpanText(null)).toBe(null)
  })
})
