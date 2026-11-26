import { describe, it, expect } from 'vitest'

import {
  LEVELS,
  LEVEL_FILTER_OPTIONS,
  LEVEL_META,
  LEVEL_RANK,
  RIVAL_TESTS,
  batchLevelOf,
  batchLevelTooltip,
  borrowedLevelOf,
  claimLevelOf,
  heldByNote,
  isLevel,
  isOpenRival,
  isShownLevel,
  isVersionOneLevel,
  levelFilterOptions,
  levelMeta,
  levelRank,
  levelTooltip,
  memberLevelTooltip,
  memberLevelsSummary,
  levelSplit,
  bestStatedLevel,
  membersAtLevel,
  commonLine,
  unstatedLines,
  readerLevel,
  rivalTestLabel,
  unlistedRivalsLine
} from '@/lib/levels'

// The one place the identification level's scale is named. The inspector, two
// ledgers, a chip and two filters read it; the failures it guards against are
// a level sorted out of the scale's order, a colour that follows the letter
// rather than the number, and a row with no level drawn as though it had one.

describe('LEVELS', () => {
  it("lists the shared scale's vocabulary in its order", () => {
    expect(LEVELS).toEqual(['1', '2a', '2b', '3a', '3b', '3c', '3d', '4a', '4b', '5a', '5b'])
  })

  it('ranks every level by its position, most identified first', () => {
    expect(LEVELS.map((level) => LEVEL_RANK[level])).toEqual(LEVELS.map((_, index) => index))
  })

  it('describes every level it names, by its own label and number', () => {
    for (const level of LEVELS) {
      expect(LEVEL_META[level]).toMatchObject({
        key: level,
        label: level,
        number: Number(level[0]),
        name: expect.any(String),
        description: expect.stringContaining(`Level ${level}:`)
      })
    }
  })

  // Colour follows the number, never the letter: 4a and 4b are two kinds of
  // evidence for a formula, not two ranks, and must not wear two colours.
  it('bands by the number: a mass, a formula, a claim about structure', () => {
    const bands = Object.fromEntries(LEVELS.map((level) => [level, LEVEL_META[level].band]))
    expect(bands).toEqual({
      1: 'structure',
      '2a': 'structure',
      '2b': 'structure',
      '3a': 'structure',
      '3b': 'structure',
      '3c': 'structure',
      '3d': 'structure',
      '4a': 'formula',
      '4b': 'formula',
      '5a': 'mass',
      '5b': 'mass'
    })
    // Drawn in the level's own tokens, never in a tier's state hue.
    expect(LEVEL_META['4a'].token).toBe('--level-formula')
    expect(LEVEL_META['5b'].token).toBe('--level-mass')
    expect(LEVEL_META['1'].token).toBe('--level-structure')
  })

  it('offers every level to a filter, in order, by its label and with its name', () => {
    expect(LEVEL_FILTER_OPTIONS.map((option) => option.value)).toEqual(LEVELS)
    for (const option of LEVEL_FILTER_OPTIONS) {
      expect(option.label).toBe(LEVEL_META[option.value].label)
      expect(option.name).toBe(LEVEL_META[option.value].name)
    }
  })
})

describe('isLevel and levelMeta', () => {
  it('accepts the scale and nothing else', () => {
    for (const level of LEVELS) expect(isLevel(level)).toBe(true)
    for (const value of [null, undefined, '', '6', '4c', '4A', 4, 'assigned']) {
      expect(isLevel(value), String(value)).toBe(false)
    }
  })

  it('does not accept an inherited property name', () => {
    expect(isLevel('constructor')).toBe(false)
    expect(isLevel('toString')).toBe(false)
  })

  // A row with no level was never judged; a fallback level would draw it as
  // a 5b.
  it('gives no chip to a row with no level, or one the scale does not name', () => {
    expect(levelMeta(null)).toBeNull()
    expect(levelMeta('6')).toBeNull()
    expect(levelMeta('constructor')).toBeNull()
    expect(levelMeta('4a')).toBe(LEVEL_META['4a'])
  })
})

describe('levelRank', () => {
  it('sorts a shuffled list into the scale', () => {
    const shuffled = ['5b', '4a', '1', '3c', '5a', '2b', '4b', '3a', '2a', '3d', '3b']
    expect([...shuffled].sort((a, b) => levelRank(a) - levelRank(b))).toEqual(LEVELS)
  })

  it('ranks no level, and an unknown one, after every level', () => {
    for (const value of [null, undefined, '6', 'constructor']) {
      expect(levelRank(value), String(value)).toBe(LEVELS.length)
    }
    expect(levelRank('5b')).toBeLessThan(levelRank(null))
  })
})

describe('levelTooltip', () => {
  it('says what the level means, what earned it and what would lift it', () => {
    expect(
      levelTooltip({ level: '5a', evidence: 'rivals not measured', lift: 'measure the shortlist' })
    ).toBe(
      [
        LEVEL_META['5a'].description,
        'Why: rivals not measured',
        'Would lift it: measure the shortlist'
      ].join('\n')
    )
  })

  it('leaves out the lines the row does not record', () => {
    expect(levelTooltip({ level: '4a' })).toBe(LEVEL_META['4a'].description)
  })

  it('says an isotopologue holds the level of its M0', () => {
    expect(levelTooltip({ level: '4b', role: 'iso_child' })).toContain(
      'An isotopologue: it holds the level of its M0'
    )
    expect(levelTooltip({ level: '4b', role: 'M0' })).not.toContain('isotopologue')
  })

  it('says nothing for a row with no level', () => {
    expect(levelTooltip({ level: null, evidence: 'x', lift: 'y' })).toBe('')
  })
})

describe('the rivals a level was read against', () => {
  it('names each test in plain words', () => {
    expect(rivalTestLabel('mass')).toBe('out by mass')
    expect(rivalTestLabel('isotopes')).toBe('out by isotopes')
    expect(rivalTestLabel('second_channel')).toBe('out by a second channel')
    expect(rivalTestLabel('open')).toBe('open')
    expect(Object.keys(RIVAL_TESTS)).toEqual(['mass', 'isotopes', 'second_channel', 'open'])
  })

  // A newer server's test is still an exclusion; reading it as open would
  // claim a rival stands that a test put out.
  it('reads a test this build does not know as an exclusion, by its key', () => {
    expect(rivalTestLabel('library_spectrum')).toBe('out by library spectrum')
    expect(rivalTestLabel('constructor')).toBe('out by constructor')
    expect(isOpenRival({ test: 'library_spectrum' })).toBe(false)
  })

  it('holds a rival open only where no test excluded it', () => {
    expect(isOpenRival({ test: 'open' })).toBe(true)
    expect(isOpenRival({ test: 'mass' })).toBe(false)
    expect(isOpenRival(null)).toBe(false)
  })

  it('notes what held an open rival back, and that it excluded nothing', () => {
    expect(heldByNote(['plausibility'])).toBe('held back by its plausibility, not excluded')
    expect(heldByNote(['prior'])).toBe("held back by the context's prior, not excluded")
    expect(heldByNote(['plausibility', 'prior'])).toBe(
      "held back by its plausibility and the context's prior, not excluded"
    )
  })

  it('notes nothing where nothing held it', () => {
    expect(heldByNote(undefined)).toBeNull()
    expect(heldByNote([])).toBeNull()
    expect(heldByNote('prior')).toBeNull()
  })

  it('counts the rivals the row knows of but does not list', () => {
    expect(unlistedRivalsLine(1, 2)).toBe('1 more rival not listed')
    expect(unlistedRivalsLine(4, 1)).toBe('4 more rivals not listed')
    // Not "more" when the row lists none.
    expect(unlistedRivalsLine(3)).toBe('3 rivals not listed')
    for (const value of [0, null, undefined, -1, 1.5]) {
      expect(unlistedRivalsLine(value, 1), String(value)).toBeNull()
    }
  })
})

describe("a batch peak's members' levels", () => {
  it('says how many members stated each level, in the scale order', () => {
    expect(memberLevelsSummary({ '4a': 3, '5a': 2 })).toBe('3 members at 4a, 2 at 5a')
    // The object's own order is not the scale's.
    expect(memberLevelsSummary({ '5a': 2, '4a': 3 })).toBe('3 members at 4a, 2 at 5a')
    expect(memberLevelsSummary({ '5b': 1 })).toBe('1 member at 5b')
  })

  it('says nothing where no member stated a level it can name', () => {
    expect(memberLevelsSummary(null)).toBeNull()
    expect(memberLevelsSummary({})).toBeNull()
    expect(memberLevelsSummary({ 6: 2, '4a': 0 })).toBeNull()
  })

  it("says the level is the members', not the batch's, and how they split", () => {
    const text = memberLevelTooltip('4a', { '4a': 3, '5a': 2 })

    expect(text.split('\n')).toEqual([
      LEVEL_META['4a'].description,
      "The level the members' runs stated for the consensus formula, not one the batch " +
        'states of its own',
      "Stated by the members' runs: 3 members at 4a, 2 at 5a"
    ])
  })

  it('says a tie reads as the weaker level, only when there is one', () => {
    expect(memberLevelTooltip('5a', { '4a': 2, '5a': 2 })).toContain(
      'A tie reads as the weaker level'
    )
    expect(memberLevelTooltip('4a', { '4a': 3, '5a': 2 })).not.toContain('tie')
  })

  it('keeps the meaning where the split is not served', () => {
    expect(memberLevelTooltip('4b', null)).toContain(LEVEL_META['4b'].description)
    expect(memberLevelTooltip('4b', null)).not.toContain('Stated by')
  })

  it('says nothing for a batch peak with no level', () => {
    expect(memberLevelTooltip(null, { '4a': 1 })).toBe('')
  })
})

// The level a reader is shown: the live claim's where a verdict states one,
// else the engine's (docs/dev/identification_claims.md, section 6.1).
describe('readerLevel and claimLevelOf', () => {
  const claim = (extra = {}) => ({
    verdict: 'confirmed',
    level: 2,
    level_letter: 'b',
    context: { claim: { evidence: 'two fragments', lift: 'a standard' } },
    ...extra
  })
  const ENGINE = { level: '4a', evidence: 'rivals out', lift: 'MS/MS' }

  it("reads a live claim's number and letter, with the claim's own lines", () => {
    expect(readerLevel({ verdict: claim(), ...ENGINE })).toEqual({
      level: '2b',
      source: 'claim',
      evidence: 'two fragments',
      lift: 'a standard',
      engineLevel: '4a'
    })
  })

  it("reads the engine's where no verdict states a level", () => {
    for (const verdict of [
      null,
      claim({ verdict: 'unsure', level: null, level_letter: null }),
      claim({ level: undefined })
    ]) {
      expect(readerLevel({ verdict, ...ENGINE })).toEqual({
        ...ENGINE,
        source: 'engine',
        engineLevel: '4a'
      })
    }
  })

  it("reads the engine's where a batch claim's window leaves the sample out", () => {
    const outside = claim({ level: 1, level_letter: null, outside_window: true })
    expect(claimLevelOf(outside)).toBeNull()
    expect(readerLevel({ verdict: outside, ...ENGINE }).source).toBe('engine')
  })

  it('reads level 1 as the scale names it, with no letter', () => {
    expect(claimLevelOf(claim({ level: 1, level_letter: null }))).toBe('1')
  })

  it("reads a version-1 claim's bare number, and no level for a letter the scale lacks", () => {
    expect(claimLevelOf(claim({ level: 3, level_letter: null }))).toBe('3')
    expect(claimLevelOf(claim({ level: 2, level_letter: 'z' }))).toBeNull()
    expect(claimLevelOf(claim({ level: 6, level_letter: null }))).toBeNull()
  })

  it('reads a claim on a row the engine gave no level', () => {
    expect(readerLevel({ verdict: claim() })).toMatchObject({ level: '2b', engineLevel: null })
    expect(readerLevel({ level: '6' })).toBeNull()
    expect(readerLevel()).toBeNull()
  })
})

describe("a version-1 claim's level", () => {
  it('is a bare number of 2 to 5, nothing else', () => {
    for (const key of ['2', '3', '4', '5']) expect(isVersionOneLevel(key), key).toBe(true)
    for (const key of ['1', '2b', '6', 2, null, 'constructor']) {
      expect(isVersionOneLevel(key), String(key)).toBe(false)
    }
    expect(isShownLevel('3')).toBe(true)
    expect(isShownLevel('3a')).toBe(true)
    expect(isLevel('3')).toBe(false)
  })

  it("draws in its number's band and says it predates the letters", () => {
    const meta = levelMeta('4')
    expect(meta).toMatchObject({ label: '4', number: 4, band: 'formula' })
    expect(meta.description).toContain("recorded before the scale's letters")
  })

  it("sorts just before its number's lettered levels", () => {
    const sorted = ['5a', '3', '2b', '1', '3a', '4'].sort((a, b) => levelRank(a) - levelRank(b))
    expect(sorted).toEqual(['1', '2b', '3', '3a', '4', '5a'])
    expect(levelRank('5')).toBeLessThan(levelRank('5a'))
  })

  it('is offered to a filter only where a row shows one, in its place', () => {
    expect(levelFilterOptions(['4a', null])).toBe(LEVEL_FILTER_OPTIONS)
    const values = levelFilterOptions(['3', '3', '4a']).map((option) => option.value)
    expect(values).toEqual([...LEVELS.slice(0, 3), '3', ...LEVELS.slice(3)])
  })
})

describe('levelTooltip source line', () => {
  it("says a claim's level is the claim's, and what the engine read", () => {
    expect(levelTooltip({ level: '2b', source: 'claim', engineLevel: '4a' }).split('\n')).toEqual([
      LEVEL_META['2b'].description,
      'Source: claim - the live verdict states this level; the engine read 4a'
    ])
    expect(levelTooltip({ level: '2b', source: 'claim' })).toContain(
      'Source: claim - the live verdict states this level'
    )
    expect(levelTooltip({ level: '2b', source: 'claim' })).not.toContain('engine read')
  })

  it("says an engine's level is the engine's", () => {
    expect(levelTooltip({ level: '4a', source: 'engine' })).toContain(
      'Source: engine - no live verdict states a level'
    )
  })
})

describe("where a batch peak's level comes from", () => {
  it("splits the members over the scale's order, the shown level marked", () => {
    expect(levelSplit({ '5a': 40, '4a': 3, '5b': 0 }, '5a')).toEqual([
      { level: '4a', count: 3, shown: false },
      { level: '5a', count: 40, shown: true }
    ])
    expect(levelSplit(null, '5a')).toEqual([])
  })

  it('names the best level any member states', () => {
    expect(bestStatedLevel({ '5a': 40, '4b': 1, '4a': 3 })).toBe('4a')
    expect(bestStatedLevel({})).toBeNull()
  })

  it("lists one level's members brightest first, one without an intensity last", () => {
    const members = [
      { sample_item_id: 'a', level: '5a', intensity: null },
      { sample_item_id: 'b', level: '4a', intensity: 9 },
      { sample_item_id: 'c', level: '5a', intensity: 3 },
      { sample_item_id: 'd', level: '5a', intensity: 7 }
    ]
    expect(membersAtLevel(members, '5a').map((m) => m.sample_item_id)).toEqual(['d', 'c', 'a'])
    expect(membersAtLevel(null, '5a')).toEqual([])
  })

  it('gives the line most members wrote and how many wrote it, a tie the brighter', () => {
    const members = [
      { level_evidence: 'one rival open', level_lift: 'measure it' },
      { level_evidence: 'two rivals open', level_lift: 'measure it' },
      { level_evidence: 'two rivals open', level_lift: null }
    ]
    expect(commonLine(members, 'level_evidence')).toEqual({
      text: 'two rivals open',
      count: 2,
      of: 3
    })
    expect(commonLine(members, 'level_lift')).toEqual({ text: 'measure it', count: 2, of: 3 })
    expect(commonLine(members.slice(0, 2), 'level_evidence').text).toBe('one rival open')
    expect(commonLine([{ level_lift: null }], 'level_lift')).toBeNull()
  })

  it('says each reason a member states no level, one line per reason', () => {
    expect(unstatedLines({ no_run: 12, no_formula: 0, other_formula: 1, no_level: 2 })).toEqual([
      '12 samples have no run of their own',
      "1 sample's run commits another formula",
      "2 samples' runs state no level for it"
    ])
    expect(unstatedLines({ no_run: 1, no_formula: 3 })).toEqual([
      '1 sample has no run of its own',
      "3 samples' runs commit no formula on this peak"
    ])
    expect(unstatedLines(null)).toEqual([])
  })
})

describe("a batch peak's own level (step 5.8c)", () => {
  const row = {
    level: '4a',
    level_evidence: "two rivals out: one by the batch's isotope checks",
    level_lift: 'MS/MS',
    member_level: '5a',
    member_levels: { '4a': 1, '5a': 3 }
  }

  it('is the ledger row level with its two lines', () => {
    expect(batchLevelOf(row)).toEqual({
      level: '4a',
      evidence: "two rivals out: one by the batch's isotope checks",
      lift: 'MS/MS'
    })
  })

  it('is none where the batch states no level of its own', () => {
    expect(batchLevelOf({ member_level: '5a' })).toBeNull()
    expect(batchLevelOf({ level: '9z' })).toBeNull()
    expect(batchLevelTooltip({ member_level: '5a' })).toBe('')
  })

  it("names the batch as its source and keeps the members' split in its hover", () => {
    const text = batchLevelTooltip(row)
    expect(text).toContain("Source: the batch - read off its members' runs")
    expect(text).toContain("Why: two rivals out: one by the batch's isotope checks")
    expect(text).toContain('Would lift it: MS/MS')
    expect(text).toContain("Stated by the members' runs: 1 member at 4a, 3 at 5a")
  })
})

describe("the batch's level on a sample's row", () => {
  const record = {
    level: '4a',
    evidence: 'the lock puts out C4H9N2O3P',
    lift: 'MS/MS',
    stated: '5a',
    borrowed: true,
    batch_level: '4a'
  }

  it("is marked as the batch's, with what the row's run stated", () => {
    const shown = borrowedLevelOf(record)
    expect(shown).toMatchObject({ level: '4a', stated: '5a', lift: 'MS/MS' })
    expect(shown.name).toBe(levelMeta('4a').name)
    expect(shown.tooltip).toContain("Source: the batch - its isotope checks read this row's")
    expect(shown.tooltip).toContain("the row's run stated 5a")
    expect(shown.tooltip).toContain('Why: the lock puts out C4H9N2O3P')
  })

  it('is none where the batch moves nothing or the record is no borrowed level', () => {
    expect(borrowedLevelOf(null)).toBeNull()
    expect(borrowedLevelOf({ ...record, borrowed: false })).toBeNull()
    expect(borrowedLevelOf({ ...record, level: 'x' })).toBeNull()
  })
})
