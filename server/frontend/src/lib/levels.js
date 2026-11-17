// The identification level a committed row carries, declared once.
//
// The level is the scale this server and peaky share: Schymanski et al.'s
// five numbers (2014) with a letter for the kind of evidence that earned
// each. It says what a reader may state of a row - a measured mass, a formula,
// a structure - where the tier says how confident the run is in its reading.
// The two cross, so the level is shown beside the tier, never instead of it,
// and nothing here moves a tier.
//
// The letters name a kind of evidence, not a rank inside a number: 4a is not
// "better" than 4b in the way 4 is better than 5. The list is still written in
// one order, the scale's, so that a level column sorts the same way in every
// ledger - most identified (1) first, a bare mass (5b) last - and the filter
// menus list the levels in the order a reader expects them.
//
// Colour follows the number alone: grey for 5 (a mass), yellow for 4 (a
// formula), green from 3 up (a structure). Two letters of one number share a
// colour, which is what keeps the colour from claiming a rank the letters do
// not have. The chip always writes the level beside its colour, so the colour
// is never the only carrier.

/**
 * The band of each number - a mass, a formula, a claim about structure - and
 * so the colour token it is drawn in (`--level-mass`, `--level-formula`,
 * `--level-structure` in style.css). The level's own tokens, apart from the
 * tier's state hues: a chip in the success and warning colours beside the tier
 * chip would read as a second tier, and a formula is no warning.
 */
const BAND_BY_NUMBER = Object.freeze({
  5: 'mass',
  4: 'formula',
  3: 'structure',
  2: 'structure',
  1: 'structure'
})

// `name` is the level's meaning in a few words, for a filter's option list and
// the inspector's heading; `description` is the one line its chip says on
// hover.
const LEVEL_DEFINITIONS = [
  {
    key: '1',
    name: 'confirmed structure',
    description: 'Level 1: a confirmed structure, assigned by a person in review'
  },
  {
    key: '2a',
    name: 'library spectrum',
    description: 'Level 2a: a probable structure, from a library spectrum match'
  },
  {
    key: '2b',
    name: 'diagnostic evidence',
    description: 'Level 2b: a probable structure, from diagnostic evidence'
  },
  {
    key: '3a',
    name: 'data and list agree',
    description: 'Level 3a: a tentative structure; the data and a list agree'
  },
  {
    key: '3b',
    name: 'one functional group',
    description: 'Level 3b: a tentative structure; one functional group'
  },
  {
    key: '3c',
    name: 'context list',
    description: 'Level 3c: a tentative structure, from a context list'
  },
  {
    key: '3d',
    name: 'homologue series',
    description: 'Level 3d: a tentative structure, from a homologue series'
  },
  {
    key: '4a',
    name: 'neutral formula',
    description:
      'Level 4a: the neutral formula; every rival excluded, and which molecule and ' +
      'reagent make the ion is pinned'
  },
  {
    key: '4b',
    name: "ion's formula",
    description:
      "Level 4b: the ion's formula; every rival excluded, but which molecule and " +
      'reagent make the ion is open'
  },
  {
    key: '5a',
    name: 'mass, rivals remain',
    description: 'Level 5a: a measured mass; rivals remain that no test excluded'
  },
  {
    key: '5b',
    name: 'mass, refuted or untestable',
    description:
      'Level 5b: a measured mass; the reading is refuted by its isotopes, chemically ' +
      'implausible, or untestable'
  }
].map((level) => {
  const number = Number(level.key[0])
  const band = BAND_BY_NUMBER[number]
  return Object.freeze({ ...level, label: level.key, number, band, token: `--level-${band}` })
})

/** Every level, in the scale's order: 1 first, 5b last. */
export const LEVELS = LEVEL_DEFINITIONS.map(({ key }) => key)

/** Level -> sort rank; ascending rank reads most identified first. */
export const LEVEL_RANK = Object.fromEntries(LEVELS.map((level, index) => [level, index]))

/** Level -> label, number, name, description, band and its colour token. */
export const LEVEL_META = Object.freeze(
  Object.fromEntries(LEVEL_DEFINITIONS.map((level) => [level.key, level]))
)

/**
 * Whether a value is one of the levels.
 *
 * Tested against the list rather than with `in` on the rank map, which walks
 * the prototype chain and would take "constructor" for a level.
 *
 * @param {*} level the level as stored on the record
 * @returns {boolean} true when the scale names it
 */
export const isLevel = (level) => LEVELS.includes(level)

/**
 * Sort rank for a level. A row with none, or one the scale does not name,
 * ranks after every level, so it never heads a column sorted best first.
 *
 * @param {*} level the level as stored on the record
 * @returns {number} its index in `LEVELS`, or `LEVELS.length`
 */
export const levelRank = (level) => (isLevel(level) ? LEVEL_RANK[level] : LEVELS.length)

/**
 * Chip presentation for a level, or null for a row that carries none.
 *
 * Null rather than a fallback level: a row with no level is not at 5b, it was
 * never judged - a reagent's ion, an imported row, a run from before the
 * level - and a grey chip would say otherwise.
 *
 * @param {*} level the level as stored on the record
 * @returns {{key: string, label: string, number: number, name: string, description: string, band: string, token: string}|null}
 */
export const levelMeta = (level) => (isLevel(level) ? LEVEL_META[level] : null)

/**
 * The hover lines of a level chip: what the level means, then what earned it
 * and what would raise it, where the row records them.
 *
 * @param {object} options
 * @param {string|null} options.level the level
 * @param {string|null} [options.evidence] the line naming what earned it
 * @param {string|null} [options.lift] what would raise it
 * @param {string|null} [options.role] the row's role; an isotopologue's level is its M0's
 * @returns {string} the lines joined, or '' for a row with no level
 */
export function levelTooltip({ level, evidence = null, lift = null, role = null }) {
  const meta = levelMeta(level)
  if (!meta) return ''
  return [
    meta.description,
    evidence ? `Why: ${evidence}` : null,
    lift ? `Would lift it: ${lift}` : null,
    role === 'iso_child' ? 'An isotopologue: it holds the level of its M0' : null
  ]
    .filter(Boolean)
    .join('\n')
}

// --- The rivals a level was read against ---------------------------------------

/**
 * How a rival's test reads, keyed by what the server writes: the test that
 * excluded it, or `open` where none did.
 */
export const RIVAL_TESTS = Object.freeze({
  mass: 'out by mass',
  isotopes: 'out by isotopes',
  second_channel: 'out by a second channel',
  open: 'open'
})

/**
 * Whether a rival still stands: no test of the run excluded it. A test this
 * build does not know is read as an exclusion it cannot name, not as open -
 * the server writes `open` for exactly the rivals nothing excluded.
 *
 * @param {object} rival one entry of `identification_level.rivals`
 * @returns {boolean}
 */
export const isOpenRival = (rival) => rival?.test === 'open'

/**
 * The words for a rival's test.
 *
 * @param {string} test the test as the server wrote it
 * @returns {string}
 */
export function rivalTestLabel(test) {
  if (Object.prototype.hasOwnProperty.call(RIVAL_TESTS, test)) return RIVAL_TESTS[test]
  return typeof test === 'string' && test ? `out by ${test.replaceAll('_', ' ')}` : 'open'
}

// What held an open rival back without excluding it.
const HELD_BY_WORDS = Object.freeze({
  plausibility: 'its plausibility',
  prior: "the context's prior"
})

/**
 * The note an open rival carries for what held it back, without excluding it:
 * its plausibility, the run's context prior, or both. Null where nothing did.
 *
 * @param {string[]|null} heldBy the rival's `held_by`
 * @returns {string|null}
 */
export function heldByNote(heldBy) {
  const words = (Array.isArray(heldBy) ? heldBy : [])
    .filter((key) => typeof key === 'string' && key)
    .map((key) =>
      Object.prototype.hasOwnProperty.call(HELD_BY_WORDS, key)
        ? HELD_BY_WORDS[key]
        : key.replaceAll('_', ' ')
    )
  if (!words.length) return null
  return `held back by ${words.join(' and ')}, not excluded`
}

/**
 * The line for the rivals a row knows of but does not list: past the stored
 * shortlist, so the level could not see a test exclude them.
 *
 * @param {number|null} count the record's `rivals_unlisted`
 * @param {number} [listed] how many rivals the row does list; "more" only after some
 * @returns {string|null} null when there are none
 */
export function unlistedRivalsLine(count, listed = 0) {
  const n = Number.isInteger(count) ? count : 0
  if (n <= 0) return null
  return `${n}${listed > 0 ? ' more' : ''} ${n === 1 ? 'rival' : 'rivals'} not listed`
}

// --- A batch peak's members ------------------------------------------------------

/**
 * The members' levels in words, in the scale's order: "3 members at 4a, 2 at
 * 5a". A level the scale does not name is left out rather than guessed at.
 *
 * @param {Object<string, number>|null} memberLevels level -> members
 * @returns {string|null} null when no member stated one
 */
export function memberLevelsSummary(memberLevels) {
  if (!memberLevels || typeof memberLevels !== 'object') return null
  const counted = LEVELS.filter(
    (level) => Number.isInteger(memberLevels[level]) && memberLevels[level] > 0
  ).map((level) => [level, memberLevels[level]])
  if (!counted.length) return null
  return counted
    .map(([level, n], index) =>
      index === 0 ? `${n} ${n === 1 ? 'member' : 'members'} at ${level}` : `${n} at ${level}`
    )
    .join(', ')
}

/**
 * The hover text of a batch peak's level chip. The level is the one most of the
 * members' runs stated for the consensus formula - the batch states none of its
 * own - so the text says whose it is and how the members split.
 *
 * @param {string|null} memberLevel the batch peak's `member_level`
 * @param {Object<string, number>|null} memberLevels its `member_levels`
 * @returns {string} '' for a batch peak with no level
 */
export function memberLevelTooltip(memberLevel, memberLevels) {
  const meta = levelMeta(memberLevel)
  if (!meta) return ''
  const counts = LEVELS.map((level) => memberLevels?.[level]).filter(
    (n) => Number.isInteger(n) && n > 0
  )
  const top = counts.length ? Math.max(...counts) : 0
  const tied = counts.filter((n) => n === top).length > 1
  const summary = memberLevelsSummary(memberLevels)
  return [
    meta.description,
    "The level the members' runs stated for the consensus formula, not one the batch " +
      'states of its own',
    summary ? `Stated by the members' runs: ${summary}` : null,
    tied ? 'A tie reads as the weaker level' : null
  ]
    .filter(Boolean)
    .join('\n')
}

/**
 * The options a level filter offers: every level in the scale's order, by its
 * label, with its name beside it so a reader picking "3c" knows what it is.
 */
export const LEVEL_FILTER_OPTIONS = Object.freeze(
  LEVELS.map((level) =>
    Object.freeze({ value: level, label: LEVEL_META[level].label, name: LEVEL_META[level].name })
  )
)
