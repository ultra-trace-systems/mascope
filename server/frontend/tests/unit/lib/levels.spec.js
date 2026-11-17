import { describe, it, expect } from 'vitest'

import {
  LEVELS,
  LEVEL_FILTER_OPTIONS,
  LEVEL_META,
  LEVEL_RANK,
  RIVAL_TESTS,
  heldByNote,
  isLevel,
  isOpenRival,
  levelMeta,
  levelRank,
  levelTooltip,
  memberLevelTooltip,
  memberLevelsSummary,
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
