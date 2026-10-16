/**
 * The time ranges the Raw files list can be narrowed to, and what to say about
 * one that turns up nothing.
 *
 * The presets are the labels the pane's selector shows, narrowest first, so an
 * empty list can offer the next one up. The store also knows a `range` mode,
 * in force whenever either date picker is set; it is not a preset and is not
 * listed here.
 */

export const ALL_TIME = 'All time'

/** The presets, narrowest first. */
export const TIME_MODES = ['Last 24 hours', 'Last 7 days', 'Last 30 days', 'Last 90 days', ALL_TIME]

/**
 * What the list opens on when the deployment configures no
 * `acquisition_filter`. Thirty days rather than a day: on a server whose
 * files are older than that the list used to open empty, with nothing to say
 * why.
 */
export const DEFAULT_TIME_MODE = 'Last 30 days'

/**
 * The look-back of a preset, in days: what the recent-files route is asked
 * for. Null for All time, for an explicit date range, and for anything that
 * is not a preset at all.
 *
 * @param {string} mode A time mode.
 * @returns {number|null}
 */
export function daysOf(mode) {
  if (mode === 'Last 24 hours') return 1
  if (mode === ALL_TIME || !TIME_MODES.includes(mode)) return null
  return Number(mode.split(' ')[1])
}

/**
 * The next wider preset: what an empty list offers in one click. Null for All
 * time, which nothing widens, and for an explicit date range, whose one step
 * out is All time.
 *
 * @param {string} mode A time mode.
 * @returns {string|null}
 */
export function widerTimeMode(mode) {
  const index = TIME_MODES.indexOf(mode)
  if (index < 0 || index === TIME_MODES.length - 1) return null
  return TIME_MODES[index + 1]
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
