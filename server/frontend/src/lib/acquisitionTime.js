/**
 * The time ranges the Raw files list can be narrowed to, and what to say about
 * one that turns up nothing.
 *
 * The presets are the labels the pane's selector shows, narrowest first, so an
 * empty list can offer the next one up. A deployment may configure another
 * look-back of the same form (`acquisition_filter = 'Last 14 days'`), which is
 * read like a preset without being offered as one. The store also knows a
 * `range` mode, in force whenever either date picker is set; it is not a
 * preset and is not listed here.
 */

export const ALL_TIME = 'All time'

/** The presets, narrowest first. */
export const TIME_MODES = ['Last 24 hours', 'Last 7 days', 'Last 30 days', 'Last 90 days', ALL_TIME]

/**
 * What the list opens on when the deployment configures no
 * `acquisition_filter`. A week: a day's window opens an empty list on a server
 * whose files are older than a day, and a wider window is a wider query on
 * every load, while the empty state offers the wider ranges in one click.
 */
export const DEFAULT_TIME_MODE = 'Last 7 days'

const LAST_DAYS = /^Last ([1-9]\d*) days$/

/**
 * The look-back of a mode, in days: what the recent-files route is asked for.
 * Any `Last N days` reads, not only the presets, since a deployment may
 * configure one. Null for All time, for an explicit date range, and for
 * anything else.
 *
 * @param {string} mode A time mode.
 * @returns {number|null}
 */
export function daysOf(mode) {
  if (mode === 'Last 24 hours') return 1
  const match = typeof mode === 'string' ? LAST_DAYS.exec(mode) : null
  return match ? Number(match[1]) : null
}

/**
 * Whether a string names a range the store can load: a preset, All time, or a
 * `Last N days` of its own.
 *
 * @param {string} mode A candidate time mode.
 * @returns {boolean}
 */
export function isTimeMode(mode) {
  return mode === ALL_TIME || daysOf(mode) !== null
}

/**
 * The next wider preset: what an empty list offers in one click. The first
 * preset longer than the look-back in force, so a configured `Last 14 days`
 * offers the last 30, and All time once no preset is longer. Null for All
 * time, which nothing widens, and for an explicit date range, whose one step
 * out is All time.
 *
 * @param {string} mode A time mode.
 * @returns {string|null}
 */
export function widerTimeMode(mode) {
  const days = daysOf(mode)
  if (days === null) return null
  return TIME_MODES.find((preset) => preset !== ALL_TIME && daysOf(preset) > days) ?? ALL_TIME
}

/**
 * How an empty list names the range in force, as a clause to end a sentence
 * with: "in the last 7 days", "in the selected date range". Null for All time,
 * where there is no range to blame.
 *
 * @param {string} mode A time mode.
 * @returns {string|null}
 */
export function describeTimeMode(mode) {
  if (mode === ALL_TIME) return null
  if (mode === 'range') return 'in the selected date range'
  return `in the ${mode.toLowerCase()}`
}
