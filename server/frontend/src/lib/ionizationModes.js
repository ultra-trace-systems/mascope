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
 */

/**
 * Derive the ionization mode dropdown for a file being processed.
 *
 * @param {object} args
 * @param {Array<object>} args.modes - Configured ionization modes
 *   (`app.data.ionization.mode.list` records).
 * @param {string} args.filename - Filename of the raw file being processed.
 * @param {string|null} args.polarity - Polarity chosen for the sample, `'+'` or
 *   `'-'`; a file whose polarity is still unknown has nothing to offer.
 * @returns {{options: Array<{label: string, value: string}>, defaultId: string|null}}
 *   Every mode in that polarity, sorted by name, plus the id to preselect -
 *   null when the filename matches no token, or more than one.
 */
export function ionizationModeChoices({ modes = [], filename = '', polarity = null } = {}) {
  const inPolarity = polarity
    ? modes.filter((mode) => mode.ionization_mode_polarity === polarity)
    : []

  const matched = inPolarity.filter(
    (mode) => mode.ionization_mode_token && (filename ?? '').includes(mode.ionization_mode_token)
  )

  return {
    options: [...inPolarity]
      .sort((a, b) => a.ionization_mode_name.localeCompare(b.ionization_mode_name))
      .map((mode) => ({
        label: mode.ionization_mode_name,
        value: mode.ionization_mode_id
      })),
    // Overlapping tokens are the one case left to the user: either match would
    // be a guess, and guessing wrong silently mismatches the whole sample.
    defaultId: matched.length === 1 ? matched[0].ionization_mode_id : null
  }
}
