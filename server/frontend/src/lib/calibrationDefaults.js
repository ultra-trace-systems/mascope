/**
 * Merge freshly fetched instrument defaults into the calibration dialog
 * parameters.
 *
 * Fields the user has not touched (still equal to the previous baseline, or
 * unknown to it) adopt the fetched instrument default; user-modified values
 * are kept.
 *
 * @param {object} params - Current dialog parameter values.
 * @param {object} baseline - Defaults the current values were seeded from.
 * @param {object} fetched - Instrument defaults fetched from the backend.
 * @returns {object} Merged parameter values.
 */
export function applyInstrumentDefaults(params, baseline, fetched) {
  const next = { ...params }
  for (const key of Object.keys(fetched)) {
    if (!(key in baseline) || params[key] === baseline[key] || params[key] == null) {
      next[key] = fetched[key]
    }
  }
  return next
}
