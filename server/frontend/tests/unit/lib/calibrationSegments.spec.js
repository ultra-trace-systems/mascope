import { describe, it, expect } from 'vitest'

import {
  calibrationSegments,
  carriedSegmentsText,
  segmentCalibrationText,
  segmentListItems,
  splitQualityIssues
} from '@/lib/calibrationSegments'

// The scan ranges of a stitched sample as the backend lists them: a reagent
// scan fitted on its own calibrants, a window that took the scan's
// calibration across their overlap, and one that took it as it is - the
// scan's factor, unchanged.
const FITTED = {
  key: 'FTMS - p NSI Full ms [40.0000-138.0000] R=120000',
  label: 'm/z 40-138',
  source: 'anchors',
  origin: null,
  calibration_factor: 1 - 11.934e-6,
  shift_ppm: null,
  shared_ions: null,
  quality: { n_points: 2 }
}
const ACROSS = {
  key: 'FTMS - p NSI Full ms [66.0000-124.0000] R=120000',
  label: 'm/z 66-124',
  source: 'overlap',
  origin: 'm/z 40-138',
  calibration_factor: 1 - 11.517e-6,
  shift_ppm: 0.417,
  shared_ions: 26,
  note: 'No calibration peaks found',
  quality: null
}
const AS_IT_IS = {
  key: 'FTMS - p NSI Full ms [132.0000-460.0000] R=120000',
  label: 'm/z 132-460',
  source: 'borrowed',
  origin: 'm/z 40-138',
  calibration_factor: 1 - 11.934e-6,
  shift_ppm: null,
  shared_ions: null,
  note: 'No suitable subset of calibration peaks found; skipping calibration.',
  quality: null
}

const record = (...segments) => ({ status: 'ok', verified: true, quality: { segments } })

describe('calibrationSegments', () => {
  it('reads the scan ranges off a fit or a record', () => {
    expect(calibrationSegments(record(FITTED, ACROSS))).toEqual([FITTED, ACROSS])
  })

  it('has none for a sample that is not stitched, or not calibrated', () => {
    expect(calibrationSegments(null)).toEqual([])
    expect(calibrationSegments(undefined)).toEqual([])
    expect(calibrationSegments({ status: 'failed' })).toEqual([])
    expect(calibrationSegments({ quality: { n_points: 3 } })).toEqual([])
  })
})

describe('segmentCalibrationText', () => {
  it('says how many calibrants a range was fitted on and how far it moved', () => {
    expect(segmentCalibrationText(FITTED)).toBe('m/z 40-138: fitted on 2 calibrants (-11.93 ppm).')
    expect(segmentCalibrationText({ ...FITTED, quality: { n_points: 1 } })).toBe(
      'm/z 40-138: fitted on 1 calibrant (-11.93 ppm).'
    )
  })

  it('does not invent a count the record does not carry', () => {
    expect(segmentCalibrationText({ ...FITTED, quality: null })).toBe(
      'm/z 40-138: fitted on its own calibrants (-11.93 ppm).'
    )
  })

  it('says whose calibration a range took across an overlap, and over how many ions', () => {
    expect(segmentCalibrationText(ACROSS)).toBe(
      'm/z 66-124: not fitted on calibrants of its own (no calibration peaks found); ' +
        'takes the calibration of m/z 40-138 across the 26 ions both ranges measure (-11.52 ppm).'
    )
  })

  it('says whose calibration a range took unchanged, and the reason recorded for it', () => {
    // Calibrants it does hold, which disagreed: the range is not short of them.
    // What the note goes on to say its own fit did next, "skipping
    // calibration", is not quoted: the range took another's instead.
    expect(segmentCalibrationText(AS_IT_IS)).toBe(
      'm/z 132-460: not fitted on calibrants of its own (no suitable subset of calibration ' +
        'peaks found); takes the calibration of m/z 40-138 unchanged (-11.93 ppm).'
    )
  })

  it('guesses at no reason where none was recorded', () => {
    expect(segmentCalibrationText({ ...ACROSS, note: null })).toBe(
      'm/z 66-124: not fitted on calibrants of its own; takes the calibration of m/z 40-138 ' +
        'across the 26 ions both ranges measure (-11.52 ppm).'
    )
    expect(segmentCalibrationText({ ...AS_IT_IS, note: undefined })).toBe(
      'm/z 132-460: not fitted on calibrants of its own; takes the calibration of ' +
        'm/z 40-138 unchanged (-11.93 ppm).'
    )
  })

  it('signs a correction upward', () => {
    expect(segmentCalibrationText({ ...FITTED, calibration_factor: 1 + 20.402e-6 })).toContain(
      '(+20.40 ppm)'
    )
  })

  it('signs no correction that rounds to nothing', () => {
    // A range the fit left where the instrument recorded it, to a hundredth
    // of a ppm, either way
    for (const off of [0.004e-6, -0.004e-6, 0]) {
      expect(segmentCalibrationText({ ...FITTED, calibration_factor: 1 + off })).toContain(
        '(0.00 ppm)'
      )
    }
  })
})

// A range's issue as the backend words it: opened with the range's label
const issue = (segment, code = 'points') => ({
  code,
  message: `${segment.label}: Fitted on 1 calibration point but moves the m/z axis.`,
  segment: segment.key
})

describe('segmentListItems', () => {
  it('lists each range once, with why it misses the bar on its line', () => {
    const items = segmentListItems({
      ...record(FITTED, ACROSS, AS_IT_IS),
      quality_issues: [issue(FITTED)]
    })

    expect(items.map((item) => item.key)).toEqual([FITTED.key, ACROSS.key, AS_IT_IS.key])
    // A fit on too few points says what it was fitted on and how far it moves
    // the axis, so it stands in for the range's own sentence
    expect(items[0].text).toBe('m/z 40-138: fitted on 1 calibration point but moves the m/z axis.')
    // Below the bar, or unchanged from another range: flagged. Across an
    // overlap: not
    expect(items.map((item) => item.flagged)).toEqual([true, false, true])
  })

  it('adds any other reason after what the range was fitted on', () => {
    const items = segmentListItems({
      ...record(FITTED),
      quality_issues: [issue(FITTED, 'residual')]
    })

    expect(items[0].text).toBe(
      'm/z 40-138: fitted on 2 calibrants (-11.93 ppm). Fitted on 1 calibration point but ' +
        'moves the m/z axis.'
    )
  })

  it('says a range with both kinds of reason once, and the second after it', () => {
    const residual = {
      code: 'residual',
      message: 'm/z 40-138: Mean m/z error after calibration is 1.20 ppm (limit 1 ppm).',
      segment: FITTED.key
    }
    const items = segmentListItems({
      ...record(FITTED),
      quality_issues: [residual, issue(FITTED)]
    })

    expect(items[0].text).toBe(
      'm/z 40-138: fitted on 1 calibration point but moves the m/z axis. Mean m/z error ' +
        'after calibration is 1.20 ppm (limit 1 ppm).'
    )
  })

  it('keeps a reason whole that does not open with the range', () => {
    const items = segmentListItems({
      ...record(FITTED),
      quality_issues: [{ code: 'x', message: 'Something else.', segment: FITTED.key }]
    })

    expect(items[0].text).toBe('m/z 40-138: fitted on 2 calibrants (-11.93 ppm). Something else.')
  })

  it('flags nothing a range has no issue with', () => {
    expect(segmentListItems(record(FITTED, ACROSS)).map((item) => item.flagged)).toEqual([
      false,
      false
    ])
    expect(segmentListItems(null)).toEqual([])
  })
})

describe('splitQualityIssues', () => {
  it('leaves the warning what is the fit’s own, and counts the ranges below the bar', () => {
    const own = { code: 'residual', message: 'Mean m/z error is 1.2 ppm.' }
    // An issue that names a range the fit does not list stays in the warning
    const elsewhere = { ...issue(FITTED), segment: 'no such range' }
    const split = splitQualityIssues({
      ...record(FITTED, ACROSS),
      quality_issues: [issue(FITTED), issue(FITTED, 'residual'), own, elsewhere]
    })

    expect(split.own).toEqual([own, elsewhere])
    expect(split.ranges).toBe(1)
  })

  it('has nothing to split without issues', () => {
    expect(splitQualityIssues(record(FITTED))).toEqual({ own: [], ranges: 0 })
    expect(splitQualityIssues(null)).toEqual({ own: [], ranges: 0 })
  })
})

describe('carriedSegmentsText', () => {
  it('names the one range that runs on a neighbouring range’s calibration', () => {
    expect(carriedSegmentsText(record(FITTED, ACROSS))).toBe(
      ' Scan range m/z 66-124 was not fitted on calibrants of its own and takes a ' +
        "neighbouring range's calibration."
    )
  })

  it('names several', () => {
    expect(carriedSegmentsText(record(FITTED, ACROSS, AS_IT_IS))).toBe(
      ' Scan ranges m/z 66-124; m/z 132-460 were not fitted on calibrants of their own ' +
        "and take a neighbouring range's calibration."
    )
  })

  it('says nothing where every range was fitted on its own', () => {
    expect(carriedSegmentsText(record(FITTED))).toBe('')
  })

  it('says nothing of a sample that is not stitched', () => {
    expect(carriedSegmentsText({ status: 'ok', quality: { n_points: 3 } })).toBe('')
    expect(carriedSegmentsText(null)).toBe('')
  })
})
