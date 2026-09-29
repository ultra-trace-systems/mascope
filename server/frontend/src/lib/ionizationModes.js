/**
 * Ionization mode choices offered when a raw file is processed by hand.
 *
 * A filename normally carries the token of the mode the file was acquired in,
 * and that mode is the one to preselect. The token is a naming convention
 * though, not a guarantee: files turn up with no token, with one nobody has
 * configured yet, or - when two configured tokens overlap - with two that both
 * match. None of those are a reason to refuse the file, so the dropdown always
 * offers every mode of the sample's polarity and an unrecognized filename
 * costs the user a preselected default, not the ability to process the file.
 *
 * A mode may belong to one instrument, and then its token only means anything
 * for that instrument's files. This mirrors the backend's own matching
 * (`api/new/ionization/modes/util.py`): a mode of another instrument is not a
 * candidate, and where an instrument has its own mode for a token the shared
 * one is not counted against it. Without that, two instruments using the same
 * token would leave every one of their files reading as ambiguous, and a file
 * of an instrument with no scoped mode could be preselected with another
 * instrument's chemistry.
 */

/**
 * Whether a file name carries the token of a configured ionization mode.
 *
 * @param {string} filename - The file's name.
 * @param {Array<object>} modes - Configured ionization modes.
 * @returns {boolean}
 */
export const carriesToken = (filename, mode) =>
  Boolean(mode.ionization_mode_token) && (filename ?? '').includes(mode.ionization_mode_token)

/**
 * An instrument name as it is compared: trimmed, lower case.
 *
 * Mirrors `method_keys.instrument_key`. Module-private: everything here that
 * decides whether two names are one instrument goes through it, and nothing
 * outside needs to - the pane takes `instrumentChoices` and `choicesForScope`
 * rather than folding names of its own.
 */
const instrumentKey = (instrument) =>
  instrument ? String(instrument).trim().toLowerCase() : null

/**
 * One option per instrument, however each name was recorded.
 *
 * `/instruments` groups by the exact spelling, so `ORBI-1` and `orbi-1` arrive
 * as two rows while the scope treats them as one instrument. Offering both would
 * invite a second mode "for the other one" that the unique index refuses anyway.
 *
 * @param {Array<{instrument: string}>} instruments - What `/instruments` returned.
 * @returns {Array<{label: string, value: string}>}
 */
export const instrumentChoices = (instruments = []) => {
  const seen = new Map()
  for (const { instrument } of instruments) {
    const key = instrumentKey(instrument)
    if (key && !seen.has(key)) seen.set(key, { label: instrument, value: instrument })
  }
  return [...seen.values()]
}

/**
 * The choices for a mode whose scope may not be among them.
 *
 * A mode scoped through the API, or to an instrument whose files are gone, would
 * otherwise show the "Every instrument" placeholder while its scope is in force.
 * Compared folded, and the stored spelling becomes the matching option's value:
 * the list keeps one spelling per instrument, so a mode stored as `orbi-1`
 * beside a listed `ORBI-1` would otherwise be offered both.
 *
 * @param {Array<{label: string, value: string}>} options - From `instrumentChoices`.
 * @param {string|null} instrument - The mode's stored scope.
 * @returns {Array<{label: string, value: string}>}
 */
export const choicesForScope = (options, instrument) => {
  if (!instrument) return options
  const key = instrumentKey(instrument)
  const match = options.find((option) => instrumentKey(option.value) === key)
  if (!match) return [...options, { label: instrument, value: instrument }]
  return options.map((option) => (option === match ? { ...option, value: instrument } : option))
}

/**
 * Whether a mode is a candidate for a file of `instrument`.
 *
 * Mirrors `applies_to_instrument`: an unscoped mode applies to every
 * instrument, a scoped one only to its own, compared folded because instrument
 * names are recorded with inconsistent case.
 *
 * One divergence from the backend, which always knows the instrument: with no
 * instrument given, every mode applies. A caller that cannot know it - the
 * upload notice runs before a file is converted - must not have scoped modes
 * disappear on it, which would report a file as carrying no configured token
 * when it carries one.
 *
 * @param {object} mode - A configured ionization mode.
 * @param {string|null} instrument - The instrument the file was acquired on, or
 *   null when the caller does not know it.
 * @returns {boolean}
 */
export const appliesToInstrument = (mode, instrument) =>
  !mode.instrument || !instrument || instrumentKey(mode.instrument) === instrumentKey(instrument)

/**
 * Whether an instrument's own token is at least as specific as a shared one.
 *
 * Mirrors `_token_covers`, one way round: the scoped token has to contain the
 * shared one. A shared `NO3_15N` beside an instrument's `NO3` is the other way
 * about - there the shared mode reads the name more precisely, so the scope
 * does not win on a shorter token and the file stays ambiguous.
 */
const tokenCovers = (scopedToken, sharedToken) =>
  Boolean(scopedToken) && Boolean(sharedToken) && scopedToken.includes(sharedToken)

/**
 * Drop an unscoped match that the instrument's own mode overrides.
 *
 * Mirrors `_prefer_scoped`, including both its conditions: the same polarity,
 * and a scoped token that covers the shared one. A name carrying two unrelated
 * tokens stays ambiguous, which is what leaves it to the user rather than
 * guessing.
 *
 * @param {Array<object>} matched - The modes whose token the name carries.
 * @returns {Array<object>}
 */
const preferScoped = (matched) => {
  const scoped = matched.filter((mode) => mode.instrument)
  if (!scoped.length) return matched
  return matched.filter(
    (mode) =>
      mode.instrument ||
      !scoped.some(
        (other) =>
          other.ionization_mode_polarity === mode.ionization_mode_polarity &&
          tokenCovers(other.ionization_mode_token, mode.ionization_mode_token)
      )
  )
}

export const hasIonizationToken = (filename, modes = [], instrument = null) =>
  modes.some((mode) => appliesToInstrument(mode, instrument) && carriesToken(filename, mode))

//: Why no mode was preselected, indexed by the number of tokens that matched
//: (two or more are all the same case). Only the count differs; the field ends
//: up empty either way, but the two say opposite things about the filename.
const RESOLUTION_REASONS = ['no-token', 'resolved', 'ambiguous']

/**
 * Derive the ionization mode dropdown for a file being processed.
 *
 * @param {object} args
 * @param {Array<object>} args.modes - Configured ionization modes
 *   (`app.data.ionization.mode.list` records).
 * @param {string} args.filename - Filename of the raw file being processed.
 * @param {string|null} args.polarity - Polarity chosen for the sample, `'+'` or
 *   `'-'`; a file whose polarity is still unknown has nothing to offer.
 * @param {string|null} args.instrument - The instrument the file was acquired
 *   on. Modes belonging to another instrument are left out entirely, and where
 *   this instrument has its own mode for a token the shared one does not count
 *   as a second match.
 * @returns {{options: Array<{label: string, value: string}>, defaultId: string|null,
 *   reason: 'resolved'|'no-token'|'ambiguous'}}
 *   Every mode in that polarity, sorted by name, plus the id to preselect -
 *   null when the filename matches no token, or more than one. `reason` says
 *   which of those two it was, so the caller can explain the empty field
 *   truthfully instead of blaming a filename that does carry a token.
 */
export function ionizationModeChoices({
  modes = [],
  filename = '',
  polarity = null,
  instrument = null
} = {}) {
  const inPolarity = polarity
    ? modes.filter(
        (mode) =>
          mode.ionization_mode_polarity === polarity && appliesToInstrument(mode, instrument)
      )
    : []

  // The override is skipped when the instrument is unknown. Every mode applies
  // then (see appliesToInstrument), so dropping the shared match would hand the
  // file whichever instrument's scoped mode happened to match - the wrong
  // chemistry, chosen for a file whose instrument nobody has established.
  const carried = inPolarity.filter((mode) => carriesToken(filename, mode))
  const matched = instrument ? preferScoped(carried) : carried

  return {
    options: [...inPolarity]
      .sort((a, b) => a.ionization_mode_name.localeCompare(b.ionization_mode_name))
      .map((mode) => ({
        label: mode.ionization_mode_name,
        value: mode.ionization_mode_id
      })),
    // Overlapping tokens are the one case left to the user: either match would
    // be a guess, and guessing wrong silently mismatches the whole sample.
    defaultId: matched.length === 1 ? matched[0].ionization_mode_id : null,
    reason: RESOLUTION_REASONS[Math.min(matched.length, 2)]
  }
}
