/**
 * The chemistry a peak-assignment run searches under, named before and after
 * the run.
 *
 * A run resolves a reagent profile (how the sample was ionized) and a chemistry
 * context (what was sampled) when it starts: `auto` reads the profile off the
 * sample's ionization mechanisms and takes the profile's own context. The run
 * records the answer on itself (`config.resolved_profile`). A launcher asks the
 * server the same question before anything starts (`fetchProfilePreview` in
 * peakAssignParams.js), and a run's provenance chip reads the recorded answer.
 * This module is how both of them say it, and reads nothing from the server.
 */

/** The identity profile and context: the profile layer switched off. */
const IDENTITY = 'none'

const POLARITY_WORDS = Object.freeze({ '+': 'positive', '-': 'negative' })

/**
 * The name a resolved profile is shown by.
 *
 * @param {object} record - a preview record or a run's `resolved_profile`
 * @returns {string}
 */
export function profileName(record) {
  if (!record?.profile) return ''
  if (record.profile === IDENTITY) return 'No profile'
  return record.profile_label || record.profile
}

/**
 * The name a resolved context is shown by.
 *
 * @param {object} record - a preview record or a run's `resolved_profile`
 * @returns {string}
 */
export function contextName(record) {
  if (!record?.context) return ''
  if (record.context === IDENTITY) return 'No context'
  return record.context_label || record.context
}

/**
 * One line for a resolution: "Bromide CIMS · Ambient air".
 *
 * @param {object} record - a preview record or a run's `resolved_profile`
 * @returns {string}
 */
export function chemistryLabel(record) {
  return [profileName(record), contextName(record)].filter(Boolean).join(' · ')
}

/**
 * The word for a polarity sign, or null for anything else.
 *
 * @param {string|null} sign - `+` or `-`
 * @returns {string|null}
 */
export const polarityWord = (sign) =>
  Object.prototype.hasOwnProperty.call(POLARITY_WORDS, sign) ? POLARITY_WORDS[sign] : null

/**
 * The preview records whose profile belongs to the other polarity from the
 * samples they count.
 *
 * A named profile is applied as named, so a bromide profile over positive
 * samples searches reagent chemistry they were never measured with. `auto`
 * cannot get this wrong; a persisted choice carried to another sample can. The
 * identity profile has no polarity and is never a mismatch.
 *
 * @param {Array<object>|null} previews
 * @returns {Array<object>}
 */
export function polarityMismatches(previews) {
  return (previews ?? []).filter(
    (record) =>
      polarityWord(record?.profile_polarity) &&
      polarityWord(record?.polarity) &&
      record.profile_polarity !== record.polarity
  )
}

/**
 * The chemistry a run recorded it searched under, or null.
 *
 * Null for every run that recorded none: an imported run, the batch ledger's
 * derived run, and a run from before profiles existed.
 *
 * @param {object|null} run - a PeakAssignmentRunRecord
 * @returns {object|null} the run's `config.resolved_profile`
 */
export function runChemistry(run) {
  const resolved = run?.config?.resolved_profile
  if (!resolved || typeof resolved !== 'object' || Array.isArray(resolved)) return null
  return typeof resolved.profile === 'string' && resolved.profile ? resolved : null
}
