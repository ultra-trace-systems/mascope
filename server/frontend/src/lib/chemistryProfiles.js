/**
 * The chemistry profiles Mascope ships, as the browser offers them.
 *
 * A profile is stored as an ionization mode with a `system_key`: a fixed
 * identity, a name, a polarity and its mechanisms, the same on every server.
 * Every one is listed from the first start, used or not, so a fresh server's
 * first file needs one click rather than a mode built by hand. A profile that
 * ships a calibrant collection - the reagent ion and its own clusters -
 * calibrates the files chosen for it on those; the ones with no reagent ship
 * none, and calibrate only once a server points them at a collection of its
 * own under Advanced.
 *
 * A server's own modes are the advanced path: they are bound by the tokens in
 * file names, and are offered after the profiles.
 */

/** What Choose chemistry heads the profiles with. */
export const PROFILES_GROUP = 'Profiles'

/** What Choose chemistry heads everything else with: the server's own modes. */
export const ADVANCED_GROUP = 'Advanced'

/**
 * Whether a mode is a profile Mascope ships.
 *
 * @param {object} mode - An ionization mode record.
 * @returns {boolean}
 */
export const isProfile = (mode) => Boolean(mode?.system_key)

/**
 * Whether a mode calibrates the files bound to it: it names a calibrant
 * collection, shipped or the server's own.
 *
 * @param {object} mode - An ionization mode record.
 * @returns {boolean}
 */
export const calibrates = (mode) => Boolean(mode?.calibration_collection_id)

/**
 * Whether a profile is ready for a file: shipped, with its calibrants.
 *
 * @param {object} mode - An ionization mode record.
 * @returns {boolean}
 */
export const isReadyProfile = (mode) => isProfile(mode) && calibrates(mode)

const byName = (a, b) => a.ionization_mode_name.localeCompare(b.ionization_mode_name)

// Negative first, as everywhere else.
const POLARITY_ORDER = { '-': 0, '+': 1 }

/**
 * The profiles, in the order the Chemistry settings list them: the ones that
 * calibrate first, then negative before positive, then by name.
 *
 * @param {Array<object>} modes - The listed ionization modes.
 * @returns {Array<object>}
 */
export const profiles = (modes = []) =>
  modes
    .filter(isProfile)
    .sort(
      (a, b) =>
        Number(calibrates(b)) - Number(calibrates(a)) ||
        (POLARITY_ORDER[a.ionization_mode_polarity] ?? 2) -
          (POLARITY_ORDER[b.ionization_mode_polarity] ?? 2) ||
        byName(a, b)
    )

/**
 * The label of a mode where a chemistry is chosen.
 *
 * A server's own mode carries its token, which is how its files are named. A
 * profile that cannot calibrate says so, so that it is not taken for one that
 * does.
 *
 * @param {object} mode - An ionization mode record.
 * @returns {string}
 */
export const chemistryLabel = (mode) => {
  if (isProfile(mode)) {
    return calibrates(mode)
      ? mode.ionization_mode_name
      : `${mode.ionization_mode_name} (no calibrants)`
  }
  return mode.ionization_mode_token
    ? `${mode.ionization_mode_name} (${mode.ionization_mode_token})`
    : mode.ionization_mode_name
}

/**
 * The choices for one polarity in Choose chemistry, as groups.
 *
 * The profiles that calibrate come first, under {@link PROFILES_GROUP}. The
 * server's own modes, and a profile that ships no calibrants, follow under
 * {@link ADVANCED_GROUP}. An empty group is left out.
 *
 * @param {Array<object>} modes - The listed ionization modes.
 * @param {string} polarity - `'-'` or `'+'`.
 * @returns {Array<{label: string, items: Array<{label: string, value: string}>}>}
 */
export function chemistryOptions(modes = [], polarity) {
  const inPolarity = modes.filter((mode) => mode.ionization_mode_polarity === polarity)
  const option = (mode) => ({ label: chemistryLabel(mode), value: mode.ionization_mode_id })
  return [
    { label: PROFILES_GROUP, items: inPolarity.filter(isReadyProfile).sort(byName).map(option) },
    {
      label: ADVANCED_GROUP,
      items: inPolarity
        .filter((mode) => !isReadyProfile(mode))
        .sort(byName)
        .map(option)
    }
  ].filter((group) => group.items.length)
}

/**
 * Every choice of {@link chemistryOptions}, out of its group.
 *
 * @param {Array<{items: Array<object>}>} groups
 * @returns {Array<{label: string, value: string}>}
 */
export const flatOptions = (groups) => groups.flatMap((group) => group.items)

/**
 * What a mode has routed, in words: how many files the server filed under it,
 * and when the newest was acquired.
 *
 * @param {object} mode - An ionization mode record, with the listing's
 *   `routed_files` and `last_file_utc`.
 * @returns {{files: string, last: string|null}}
 */
export function routedSummary(mode) {
  const count = mode?.routed_files ?? 0
  const files = count === 0 ? 'No files yet' : count === 1 ? '1 file' : `${count} files`
  const date = mode?.last_file_utc ? new Date(mode.last_file_utc) : null
  const last = date && !Number.isNaN(date.getTime()) ? date.toLocaleString() : null
  return { files, last }
}
