/**
 * The settings of an m/z calibration fit, as the calibration dialog shows
 * them: the parameter each field edits, its name, its unit where it has one,
 * and the range its input allows. The settings pane lays them out in this
 * order, and the dialog's folded settings name them in one line.
 */
export const CALIBRATION_FIELDS = [
  { key: 'match_score_min', name: 'Min. match score', range: { min: 0, max: 1, step: 0.1 } },
  {
    key: 'refine_window',
    name: 'Refine window',
    unit: 'ppm',
    range: { min: 0, max: 1000, step: 1 }
  },
  {
    key: 'isotope_abundance_min',
    name: 'Min. isotope abundance',
    range: { min: 0, max: 1, step: 0.05 }
  },
  {
    key: 'peak_intensity_min',
    name: 'Min. peak intensity',
    range: { min: 0, max: 100000, step: 1000 }
  },
  {
    key: 'snr_threshold',
    name: 'Min. signal-to-noise ratio',
    range: { min: 0, max: 1000, step: 10 }
  }
]

/**
 * A field's label in the settings pane: its name, and its unit in brackets.
 *
 * @param {object} field - an entry of `CALIBRATION_FIELDS`
 * @returns {string}
 */
export function calibrationFieldLabel(field) {
  return field.unit ? `${field.name} [${field.unit}]` : field.name
}

/**
 * The settings a fit runs with, in one line.
 *
 * @param {object} params - the dialog's `mzCalibrationParams`
 * @returns {string} e.g. "Min. match score 0 · Refine window 50 ppm · ..."; a
 *   value not set yet reads as a dash
 */
export function calibrationSettingsText(params) {
  return CALIBRATION_FIELDS.map(({ key, name, unit }) => {
    const value = params?.[key]
    if (value === null || value === undefined) return `${name} –`
    return unit ? `${name} ${value} ${unit}` : `${name} ${value}`
  }).join(' · ')
}
