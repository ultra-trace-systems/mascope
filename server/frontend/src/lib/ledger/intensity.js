/**
 * The ledger's intensity cell: a compact number and a log-scale bar
 * (docs/dev/assignment_ledger_and_card.md, section 3.2).
 *
 * A peak's intensity spans five or six decades within one sample, so the
 * exponent form the ledger used to show (`6.751E2`) made a reader parse every
 * number to compare two. The compact form reads at a glance (`675`, `8.31k`,
 * `1.20M`) and keeps three significant digits, as many as the eye compares
 * down a column; the bar beside it is on a log scale, so a row a decade
 * brighter is a step longer rather than ten times longer.
 */

// The prefixes, a thousand apart. An intensity past them (a summed TOF area
// can reach 1e13) keeps the last one.
const UNITS = Object.freeze(['', 'k', 'M', 'G', 'T'])

/**
 * An intensity as a compact number: three significant digits, with the
 * thousands as `k`, `M`, `G`, `T`. Below a thousand the number is as it is,
 * to three significant digits and no trailing zeros (`19`, `0.532`, `675`);
 * from a thousand the trailing zeros stay, so every scaled number has three
 * digits (`1.20M`, `8.31k`, `12.0k`).
 *
 * @param {number|null|undefined} value
 * @returns {string} '' for a value that is not a finite number
 */
export function compactNumber(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return ''
  if (value === 0) return '0'
  const sign = value < 0 ? '-' : ''
  const magnitude = Math.abs(value)
  if (magnitude < 1000) {
    // toPrecision rounds 999.6 up to "1.00e+3": that is a thousand.
    const rounded = Number(magnitude.toPrecision(3))
    if (rounded < 1000) return `${sign}${rounded}`
  }
  let unit = Math.min(Math.floor(Math.log10(magnitude) / 3), UNITS.length - 1)
  let scaled = magnitude / 1000 ** unit
  // 999,950 is 999.95k, which three digits round to a thousand k: a million.
  if (Number(scaled.toPrecision(3)) >= 1000 && unit < UNITS.length - 1) {
    unit += 1
    scaled = magnitude / 1000 ** unit
  }
  return `${sign}${scaled.toPrecision(3)}${UNITS[unit]}`
}

/**
 * The range the bars of a scope are drawn over: the faintest and the
 * brightest positive intensity among the values, or null where none is.
 *
 * Read over every row the scope holds rather than over the rows a filter
 * leaves, so a bar keeps its length while the reader narrows the ledger.
 *
 * @param {Iterable<number>} values
 * @returns {[number, number]|null}
 */
export function intensityDomain(values) {
  let lo = Infinity
  let hi = -Infinity
  for (const value of values) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) continue
    if (value < lo) lo = value
    if (value > hi) hi = value
  }
  return hi > 0 ? [lo, hi] : null
}

/**
 * How long an intensity's bar is, as a share of the cell, on a log scale
 * over the scope's domain. The faintest row's bar is not empty but a sliver,
 * so a bar always says there is a peak; a domain of one value draws every
 * bar whole.
 *
 * @param {number|null|undefined} value
 * @param {[number, number]|null} domain - from `intensityDomain`
 * @returns {number} 0 (no bar) to 1
 */
export function intensityBarShare(value, domain) {
  if (!domain || typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return 0
  const [lo, hi] = domain
  if (!(hi > lo)) return 1
  const share = (Math.log10(value) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))
  return Math.min(1, Math.max(MIN_BAR, MIN_BAR + (1 - MIN_BAR) * share))
}

// The faintest row's bar.
const MIN_BAR = 0.04
