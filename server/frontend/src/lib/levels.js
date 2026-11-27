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

// --- A claim recorded before the letters ---------------------------------------
//
// A claim made under version 1 of the claims' mapping (docs/dev/
// identification_claims.md, section 5.4) records its number and no letter, and
// nothing backfills one. Level 1 has no letter on the scale, so it reads as
// itself; a bare 2, 3 or 4 is no level the scale names, and guessing a letter
// for it would state evidence nobody recorded. It reads as the number alone,
// in its number's colour, with a hover line that says why it has no letter, and
// sorts just before its number's lettered levels.

const VERSION_ONE_META = Object.freeze(
  Object.fromEntries(
    [2, 3, 4, 5].map((number) => {
      const key = String(number)
      const band = BAND_BY_NUMBER[number]
      const first = LEVELS.findIndex((level) => level[0] === key)
      return [
        key,
        Object.freeze({
          key,
          label: key,
          number,
          band,
          token: `--level-${band}`,
          name: 'recorded before the letters',
          description:
            `Level ${number}: stated by a claim recorded before the scale's letters ` +
            "(version 1 of the claims' mapping), so it names no kind of evidence",
          rank: first - 0.5,
          versionOne: true
        })
      ]
    })
  )
)

/**
 * Whether a value is a version-1 claim's level: a number of the scale with no
 * letter, where the scale names that number only with one.
 *
 * @param {*} level the level as a reader is shown it
 * @returns {boolean}
 */
export const isVersionOneLevel = (level) =>
  typeof level === 'string' && Object.prototype.hasOwnProperty.call(VERSION_ONE_META, level)

/**
 * Whether a reader can be shown the value as a level: one of the scale's, or a
 * version-1 claim's bare number.
 *
 * @param {*} level the level as a reader is shown it
 * @returns {boolean}
 */
export const isShownLevel = (level) => isLevel(level) || isVersionOneLevel(level)

/**
 * Sort rank for a level. A row with none, or one the scale does not name,
 * ranks after every level, so it never heads a column sorted best first.
 *
 * @param {*} level the level as stored on the record
 * @returns {number} its index in `LEVELS`, or `LEVELS.length`
 */
export const levelRank = (level) =>
  isLevel(level)
    ? LEVEL_RANK[level]
    : isVersionOneLevel(level)
      ? VERSION_ONE_META[level].rank
      : LEVELS.length

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
export const levelMeta = (level) =>
  isLevel(level) ? LEVEL_META[level] : isVersionOneLevel(level) ? VERSION_ONE_META[level] : null

/**
 * What a 5a row's lift says where no pass measured its rivals, as the engine
 * writes it now (`identification_level.LIFT_RIVALS_UNMEASURED`).
 */
export const LIFT_RIVALS_UNMEASURED = 'its rivals measured: this run did not measure them'

// Lift parts runs stored in words that named no step a reader can take, and
// what they read as now. Before MAS-1336 a row whose rivals were not measured
// asked to "measure the shortlist": the run option that does it is offered by
// no setting of the app, so the line now says the rivals were not measured.
// A row keeps what its run stored, so the old words are read here.
const LIFT_REWORDED = Object.freeze({ 'measure the shortlist': LIFT_RIVALS_UNMEASURED })

/**
 * A level's lift as a reader is shown it: the parts an older run stored in
 * words no reader could act on, reworded (LIFT_REWORDED), the rest as stored.
 *
 * @param {?string} lift the stored lift, its parts joined by "; "
 * @returns {?string}
 */
export function readableLift(lift) {
  if (typeof lift !== 'string' || !lift) return lift ?? null
  return lift
    .split('; ')
    .map((part) => LIFT_REWORDED[part] ?? part)
    .join('; ')
}

/**
 * The hover lines of a level chip: what the level means, then what earned it
 * and what would raise it, where the row records them.
 *
 * @param {object} options
 * @param {string|null} options.level the level
 * @param {string|null} [options.evidence] the line naming what earned it
 * @param {string|null} [options.lift] what would raise it
 * @param {string|null} [options.role] the row's role; an isotopologue's level is its M0's
 * @param {'claim'|'engine'|null} [options.source] whose level it is, where a
 *   reader could be shown either (readerLevel); left out where only one can be
 * @param {string|null} [options.engineLevel] the engine's level, under a claim
 * @returns {string} the lines joined, or '' for a row with no level
 */
export function levelTooltip({
  level,
  evidence = null,
  lift = null,
  role = null,
  source = null,
  engineLevel = null
}) {
  const meta = levelMeta(level)
  if (!meta) return ''
  return [
    meta.description,
    source ? sourceLine(source, engineLevel) : null,
    evidence ? `Why: ${evidence}` : null,
    lift ? `Would lift it: ${readableLift(lift)}` : null,
    role === 'iso_child' ? 'An isotopologue: it holds the level of its M0' : null
  ]
    .filter(Boolean)
    .join('\n')
}

// Whose level a chip shows: the live claim's, or the engine's.
function sourceLine(source, engineLevel) {
  if (source === 'claim') {
    const engine = levelMeta(engineLevel)
    return (
      'Source: claim - the live verdict states this level' +
      (engine ? `; the engine read ${engine.label}` : '')
    )
  }
  return 'Source: engine - no live verdict states a level'
}

/**
 * The level a reader is shown for a row: the live claim's where a verdict
 * states one, else the engine's (docs/dev/identification_claims.md, section 3
 * rule 4 and section 6.1). A verdict states a level when it carries an integer
 * `level`: a confirmation that names a structure, a class or the formula under
 * the claims' mapping. A rejection, an unsure verdict, a confirmation on visual
 * evidence and a refuting standard state none, and nor does a batch-level claim
 * served to a sample its window leaves out: the server sends that one with no
 * level and `outside_window`, so the row reads the engine's there.
 *
 * The claim's level is its number and the scale's letter (`level_letter`); one
 * recorded under version 1 has no letter and reads as the number alone
 * (isVersionOneLevel). Its two lines are the claim's own, from `context.claim`.
 *
 * @param {object} options
 * @param {?object} [options.verdict] the live verdict that reaches the row: its
 *   own, else the batch-level one
 * @param {?string} [options.level] the engine's level
 * @param {?string} [options.evidence] the line naming what earned the engine's
 * @param {?string} [options.lift] what would raise the engine's
 * @returns {?{level: string, source: 'claim'|'engine', evidence: ?string, lift: ?string, engineLevel: ?string}}
 *   null where neither states a level the scale can show
 */
export function readerLevel({ verdict = null, level = null, evidence = null, lift = null } = {}) {
  const claimed = claimLevelOf(verdict)
  const engineLevel = isLevel(level) ? level : null
  if (claimed) {
    const claim = verdict.context?.claim
    const lines = claim && typeof claim === 'object' ? claim : {}
    return {
      level: claimed,
      source: 'claim',
      evidence: lines.evidence ?? null,
      lift: lines.lift ?? null,
      engineLevel
    }
  }
  if (!engineLevel) return null
  return { level: engineLevel, source: 'engine', evidence, lift: readableLift(lift), engineLevel }
}

/**
 * The level a verdict states, as the chip writes it, or null where it states
 * none: a number with its letter (`2b`), level 1, or a version-1 claim's bare
 * number. A number with a letter the scale does not name is no level to show.
 *
 * @param {?object} verdict a verification record
 * @returns {?string}
 */
export function claimLevelOf(verdict) {
  if (!verdict || verdict.outside_window || !Number.isInteger(verdict.level)) return null
  const key = `${verdict.level}${verdict.level_letter ?? ''}`
  return isShownLevel(key) ? key : null
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
 * The hover text of a batch peak's level chip where the batch states no level
 * of its own (its level stage has not run, or its record no longer stands): the
 * one most of the members' runs stated for the consensus formula, so the text
 * says whose it is and how the members split.
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

// --- Where a batch peak's level comes from ---------------------------------------

/**
 * How a batch peak's members split over the levels their runs stated, in the
 * scale's order, the level the ledger shows for them marked.
 *
 * @param {Object<string, number>|null} memberLevels level -> members
 * @param {string|null} shown the members' level the ledger shows (`member_level`)
 * @returns {Array<{level: string, count: number, shown: boolean}>}
 */
export function levelSplit(memberLevels, shown) {
  if (!memberLevels || typeof memberLevels !== 'object') return []
  return LEVELS.filter(
    (level) => Number.isInteger(memberLevels[level]) && memberLevels[level] > 0
  ).map((level) => ({ level, count: memberLevels[level], shown: level === shown }))
}

/**
 * The best level any member states: the first of the scale's order.
 *
 * @param {Object<string, number>|null} memberLevels level -> members
 * @returns {string|null}
 */
export function bestStatedLevel(memberLevels) {
  return levelSplit(memberLevels, null)[0]?.level ?? null
}

/**
 * The members at one level, brightest first, as the level-sources read
 * serves them (`GET /batch-peaks/batch/{id}/levels/{batch_peak_id}`).
 *
 * @param {Array<object>|null} members its `data`
 * @param {string} level
 * @returns {Array<object>}
 */
export function membersAtLevel(members, level) {
  return (Array.isArray(members) ? members : [])
    .filter((member) => member?.level === level)
    .sort((a, b) => (b.intensity ?? -Infinity) - (a.intensity ?? -Infinity))
}

/**
 * The line most of the members say, of one of a level's two lines, and how
 * many say it. Equal counts keep the line of the brighter member, so the
 * choice is stable from read to read.
 *
 * @param {Array<object>} members the members at one level, brightest first
 * @param {'level_evidence'|'level_lift'} key which line
 * @returns {{text: string, count: number, of: number}|null} null where no
 *   member wrote one
 */
export function commonLine(members, key) {
  const counts = new Map()
  for (const member of members ?? []) {
    const text = member?.[key]
    if (typeof text === 'string' && text) counts.set(text, (counts.get(text) ?? 0) + 1)
  }
  let best = null
  for (const [text, count] of counts) if (!best || count > best.count) best = { text, count }
  if (!best) return null
  // A lift as a reader is shown it: an older run's words reworded.
  const text = key === 'level_lift' ? readableLift(best.text) : best.text
  return { ...best, text, of: (members ?? []).length }
}

/**
 * The members of a batch peak that state no level of its consensus, in words,
 * one line per reason the level-sources read counts (`unstated`).
 *
 * @param {Object<string, number>|null} unstated why -> members
 * @returns {string[]}
 */
export function unstatedLines(unstated) {
  const one = (n) => n === 1
  const runs = (n, verb) => (one(n) ? `1 sample's run ${verb}s` : `${n} samples' runs ${verb}`)
  const words = {
    no_run: (n) =>
      one(n) ? '1 sample has no run of its own' : `${n} samples have no run of their own`,
    no_formula: (n) => `${runs(n, 'commit')} no formula on this peak`,
    other_formula: (n) => `${runs(n, 'commit')} another formula`,
    no_level: (n) => `${runs(n, 'state')} no level for it`
  }
  return Object.entries(words)
    .filter(([why]) => Number.isInteger(unstated?.[why]) && unstated[why] > 0)
    .map(([why, line]) => line(unstated[why]))
}

/**
 * The level a batch peak states of its own (step 5.8c), read off its members'
 * runs and the batch's isotope checks: the ledger's `level` with its two lines,
 * or null where no level of the batch stands and the members' level is all the
 * row has.
 *
 * @param {object} row a batch ledger row
 * @returns {?{level: string, evidence: ?string, lift: ?string}}
 */
export function batchLevelOf(row) {
  if (!isLevel(row?.level)) return null
  return {
    level: row.level,
    evidence: row.level_evidence ?? null,
    lift: readableLift(row.level_lift ?? null)
  }
}

/**
 * The hover text of a batch peak's own level chip: the level, whose it is,
 * what earned it and what would lift it, and how the members' runs split.
 *
 * @param {object} row a batch ledger row with `level`, `level_evidence`,
 *   `level_lift` and `member_levels`
 * @returns {string} '' for a batch peak with no level of its own
 */
export function batchLevelTooltip(row) {
  const own = batchLevelOf(row)
  if (!own) return ''
  const summary = memberLevelsSummary(row.member_levels)
  return [
    levelMeta(own.level).description,
    "Source: the batch - read off its members' runs and its isotope checks",
    own.evidence ? `Why: ${own.evidence}` : null,
    own.lift ? `Would lift it: ${own.lift}` : null,
    summary ? `Stated by the members' runs: ${summary}` : null
  ]
    .filter(Boolean)
    .join('\n')
}

/**
 * The batch's level on a sample's row, where the batch's isotope checks move
 * the row's own (`batch_level` of the detail read): the level, marked as the
 * batch's, with the level the row's run stated. Null where the batch moves
 * nothing.
 *
 * @param {?object} record the detail's `batch_level`
 * @returns {?{level: string, name: string, evidence: ?string, lift: ?string, stated: ?string, tooltip: string}}
 */
export function borrowedLevelOf(record) {
  const meta = levelMeta(record?.level)
  if (!meta || !record.borrowed) return null
  const stated = isLevel(record.stated) ? record.stated : null
  return {
    level: record.level,
    name: meta.name,
    evidence: record.evidence ?? null,
    lift: readableLift(record.lift ?? null),
    stated,
    tooltip: [
      meta.description,
      "Source: the batch - its isotope checks read this row's own rivals" +
        (stated ? `; the row's run stated ${stated}` : ''),
      record.evidence ? `Why: ${record.evidence}` : null,
      record.lift ? `Would lift it: ${readableLift(record.lift)}` : null
    ]
      .filter(Boolean)
      .join('\n')
  }
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

/**
 * A level filter's options for the levels a ledger shows: the scale's, and in
 * its place a version-1 claim's bare number where a row shows one, so a reader
 * can filter on every level the column draws.
 *
 * @param {Iterable<string>} shown the levels the ledger's rows show
 * @returns {Array<{value: string, label: string, name: string}>}
 */
export function levelFilterOptions(shown) {
  const extra = [...new Set(shown)].filter(isVersionOneLevel)
  if (!extra.length) return LEVEL_FILTER_OPTIONS
  return [
    ...LEVEL_FILTER_OPTIONS,
    ...extra.map((level) => ({
      value: level,
      label: VERSION_ONE_META[level].label,
      name: VERSION_ONE_META[level].name
    }))
  ].sort((a, b) => levelRank(a.value) - levelRank(b.value))
}
