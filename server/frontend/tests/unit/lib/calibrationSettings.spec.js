import { describe, it, expect } from 'vitest'

import {
  CALIBRATION_FIELDS,
  calibrationFieldLabel,
  calibrationSettingsText
} from '@/lib/calibrationSettings'

const PARAMS = {
  match_score_min: 0,
  refine_window: 50,
  isotope_abundance_min: 0.15,
  peak_intensity_min: 0,
  snr_threshold: 50
}

describe('calibrationSettingsText', () => {
  it('names every setting with its value and unit, in the order the pane shows them', () => {
    expect(calibrationSettingsText(PARAMS)).toBe(
      'Min. match score 0 · Refine window 50 ppm · Min. isotope abundance 0.15 · ' +
        'Min. peak intensity 0 · Min. signal-to-noise ratio 50'
    )
  })

  it('reads a setting not set yet as a dash', () => {
    expect(calibrationSettingsText({ ...PARAMS, refine_window: null })).toContain('Refine window –')
    expect(calibrationSettingsText(undefined).split(' · ')).toHaveLength(CALIBRATION_FIELDS.length)
  })
})

describe('calibrationFieldLabel', () => {
  it('puts a unit in brackets, as the pane always labelled it', () => {
    expect(CALIBRATION_FIELDS.map(calibrationFieldLabel)).toEqual([
      'Min. match score',
      'Refine window [ppm]',
      'Min. isotope abundance',
      'Min. peak intensity',
      'Min. signal-to-noise ratio'
    ])
  })
})
