import { customAlphabet } from 'nanoid'

// STRINGS

export function beautifySnakeCase(str) {
  // Replace underscores with white space and capitalize first letter
  return capitalizeFirstLetter(str.replaceAll('_', ' '))
}

export function strToSnakeCase(str) {
  // Convert any string to snake_case
  return str
    .replace(/\W+/g, ' ')
    .split(/ |\B(?=[A-Z])/)
    .map((word) => word.toLowerCase())
    .join('_')
}

export function capitalizeFirstLetter(str) {
  // Capitalize first letter of a string
  return str[0].toUpperCase() + str.slice(1)
}

/**
 * Beautify an upper case SNAKE_CASE string by converting it to a capitalized, readable label.
 * e.g., 'FILTER_REGENERATION' -> 'Filter Regeneration'
 */
export function beautifyConstant(str) {
  if (!str) return str
  return str
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ')
}

/**
 * Normalize a string by stripping leading/trailing spaces, normalizing internal whitespace,
 * and optionally converting to lowercase. Matches backend norm() function in
 * libraries\file\src\mascope_file\string.py.
 *
 * @param {string} str - The string to normalize
 * @param {boolean} lower - Whether to convert to lowercase (default: false)
 * @returns {string} The normalized string
 *
 * Examples:
 * norm("  Hello   World  ") → "Hello World"
 * norm("  Hello   World  ", true) → "hello world"
 */
export function norm(str, lower = false) {
  if (!str) return ''
  const normalized = str.trim().split(/\s+/).join(' ')
  return lower ? normalized.toLowerCase() : normalized
}

export function prettyTrim(label, length = 15) {
  return label && label.length > length ? label.slice(0, length) + '...' : label
}

// MISC

export function genId(len, case_sensitive = true) {
  let alphabet
  if (case_sensitive) {
    alphabet = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
  } else {
    alphabet = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
  }
  const nanoid = customAlphabet(alphabet, len)
  return nanoid()
}

export function clone(object) {
  return object ? JSON.parse(JSON.stringify(object)) : object
}

/**
 * PrimeVue's severity for a notification status: its Message and Toast call
 * a warning `warn`, and name the others as the server does.
 *
 * @param {string} status A notification status: success, info, warning or error.
 * @returns {string} The severity to render it with.
 */
export const messageSeverity = (status) => ({ warning: 'warn' })[status] ?? status

export function debounce(callback, timeout = 500) {
  let timeoutId = null
  return (...args) => {
    clearTimeout(timeoutId)
    timeoutId = setTimeout(() => {
      callback(...args)
    }, timeout)
  }
}

/**
 * Instrument class of a sample: what the reader recorded when the file was
 * converted, which every sample row carries as `instrument_type`; the name
 * rule of `instrumentType` for rows that predate the field.
 *
 * @param {object|null|undefined} sample A sample row (or sample file row)
 * @returns {'orbi'|'tof'|null}
 */
export function sampleInstrumentType(sample) {
  return sample?.instrument_type ?? instrumentType(sample?.instrument)
}

/**
 * Whether the server would accept this as an instrument name: letters, digits
 * and hyphens, up to 64 of them. The underscore is the separator between an
 * instrument and the rest of a file name, so it cannot be part of one.
 * Mirrors `INSTRUMENT_NAME_RE` in the backend.
 *
 * @param {string|null|undefined} instrument An instrument name
 * @returns {boolean}
 */
export function isValidInstrumentName(instrument) {
  return typeof instrument === 'string' && /^[A-Za-z0-9-]{1,64}$/.test(instrument)
}

export function instrumentType(instrument) {
  if (!instrument) {
    return null
  }
  const name = instrument.toLowerCase()
  if (name.includes('orbi')) {
    return 'orbi'
  } else if (name.includes('tof') || name.includes('api')) {
    return 'tof'
  } else {
    return null
  }
}
