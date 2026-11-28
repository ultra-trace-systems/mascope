import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { hiddenAt, outOfSightAt, parseNarrowRules, styleAt } from './narrowRules.js'

// The ledger at narrow widths (docs/dev/assignment_ledger_and_card.md, section
// 3.3): a container query drops the Level column, then the samples, then the
// batch's badges, then the intensity's bar, and the level's chips show in the
// Tier cell only where the Level column is gone. Two cells narrow instead of
// dropping: the tier chip's word goes from sight with the badges, and the
// Intensity column's minimum width with the bar. jsdom evaluates no container
// query, so these read the rules the ledger loads and work out what each width
// hides.

const source = (...path) =>
  readFileSync(join(import.meta.dirname, '..', '..', '..', '..', 'src', 'lib', ...path), 'utf8')
const LEDGER = source('ledger', 'AssignmentLedger.vue')
const TIER_TAG = source('base', 'BaseTierTag.vue')

describe('the narrow ledger', () => {
  // The rules apply only where the ledger loads them and names itself the
  // container they measure.
  it('is loaded by the ledger, which is the container it measures', () => {
    expect(LEDGER).toMatch(/^import '\.\/narrow\.css'$/m)
    expect(LEDGER).toMatch(/\.ledger \{[^}]*container: ledger \/ inline-size;/)
  })

  // A scoped rule weighs as much as `.ledger .x`, or more, and comes later in
  // the bundle, so one that sets a property of a class these rules set would
  // win at every width (the intensity's bar kept its display so). That holds
  // for the ledger's styles and for the tier chip's, whose word they hide.
  it.each([
    ['the ledger', LEDGER],
    ['the tier chip', TIER_TAG]
  ])("is not overridden by %s's own style of what it sets", (_, file) => {
    // A rule under :where() weighs nothing and is exempt: that is how the
    // ledger gives the bar its display.
    const style = file.slice(file.indexOf('<style')).replace(/\/\*[\s\S]*?\*\//g, '')
    for (const { selector, declarations } of parseNarrowRules()) {
      const cls = selector.split(' ').at(-1).replace('.', '\\.')
      for (const property of Object.keys(declarations)) {
        const own = new RegExp(
          `(^|\\})(?!\\s*:where\\()[^{}]*${cls}(?![\\w-])[^{}]*\\{[^}]*(^|[\\s;])${property}\\s*:`
        )
        expect(style, `${selector} ${property}`).not.toMatch(own)
      }
    }
  })

  // The minimum widths the narrow rules lower are theirs alone: an inline
  // style on the Column would hold against every query.
  it('leaves no inline minimum width on the Tier and Intensity columns', () => {
    for (const cls of ['col-tier', 'col-intensity']) {
      const column = LEDGER.match(new RegExp(`<Column[^>]*headerClass="${cls}"[^>]*>`))
      expect(column, cls).not.toBeNull()
      expect(column[0], cls).toContain(`bodyClass="${cls}"`)
      expect(column[0], cls).not.toMatch(/style=/)
    }
  })

  it('reads every rule it holds', () => {
    expect(parseNarrowRules()).toHaveLength(12)
  })

  it('drops the Level column first, then the samples, then the badges, then the bar', () => {
    const drops = parseNarrowRules()
      .filter((rule) => rule.maxWidth != null && rule.effect === 'drop')
      .sort((a, b) => b.maxWidth - a.maxWidth)
    expect(drops.map((rule) => rule.selector)).toEqual([
      '.ledger .col-level',
      '.ledger .col-samples',
      '.ledger .col-across',
      '.ledger .intensity-bar'
    ])
  })

  // The level moves rather than drops: its chips in the Tier cell are hidden
  // at exactly the widths that keep the Level column, and shown at exactly the
  // widths that drop it, so a row carries the level once at every width.
  it('shows the level in the Tier cell exactly where the Level column is gone', () => {
    for (let width = 300; width <= 1600; width++) {
      const hidden = hiddenAt(width)
      const column = hidden.includes('.ledger .col-level')
      const inTier = hidden.includes('.ledger .tier-level')
      expect(column !== inTier, `at ${width} px`).toBe(true)
    }
  })

  it('keeps every cell at a wide width, and drops them in order as it narrows', () => {
    // The Intensity header's short label is the one thing dropped at a wide
    // width: its full label stands there.
    const short = '.ledger .intensity-label-short'
    expect(hiddenAt(1200)).toEqual(['.ledger .tier-level', short])
    expect(hiddenAt(901)).toEqual(['.ledger .tier-level', short])
    expect(hiddenAt(900)).toEqual([short, '.ledger .col-level'])
    expect(hiddenAt(850)).toEqual([short, '.ledger .col-level'])
    expect(hiddenAt(640)).toEqual([
      short,
      '.ledger .col-level',
      '.ledger .col-samples',
      '.ledger .col-across'
    ])
    expect(hiddenAt(400)).toEqual([
      '.ledger .col-level',
      '.ledger .col-samples',
      '.ledger .col-across',
      '.ledger .intensity-bar'
    ])
  })

  // The tier chip shows its dot alone with the badges gone, at 680 px and
  // below, and its word above. Taken from sight, not dropped: the word stays
  // the chip's accessible name. A role chip's word is no `tier-word`.
  it("takes the tier's word from sight at 680 px and below, and not above", () => {
    const word = '.ledger .tier-cell .tier-word'
    for (let width = 300; width <= 1600; width++) {
      expect(outOfSightAt(width).includes(word), `at ${width} px`).toBe(width <= 680)
      expect(hiddenAt(width), `at ${width} px`).not.toContain(word)
    }
  })

  // A rule that clips an element from sight takes it out of the layout too:
  // the absolute 1 px box is what narrows the column, so a clip without it
  // hides the word and leaves the column as wide as before.
  it('takes every clipped element out of the layout as well as from sight', () => {
    const clipped = parseNarrowRules().filter((rule) => rule.declarations['clip-path'])
    expect(clipped.map((rule) => rule.selector)).toEqual([
      '.ledger .tier-cell .tier-word',
      '.ledger .intensity-label'
    ])
    for (const rule of clipped) {
      expect(rule.effect, rule.selector).toBe('sight')
      expect(rule.declarations, rule.selector).toMatchObject({
        position: 'absolute',
        width: '1px',
        height: '1px',
        overflow: 'hidden',
        'clip-path': 'inset(50%)'
      })
    }
  })

  // Hiding the word narrows nothing while the column holds its 7 rem: its
  // minimum gives way at the same width.
  it.each([
    ['.ledger .col-tier', 680],
    ['.ledger .col-intensity', 560]
  ])('lowers the minimum width of %s at %i px and below', (selector, at) => {
    for (let width = 300; width <= 1600; width++) {
      expect(styleAt(selector, 'min-width', width), `at ${width} px`).toBe(
        width <= at ? '0' : '7rem'
      )
    }
  })

  // With its bar gone the Intensity header is no wider than its number: its
  // short label in sight, its full label taken from sight but kept as its
  // name, at exactly the widths that lower the column's minimum width.
  it('shows the short Intensity label exactly where the full one leaves sight', () => {
    // In half pixels too: a ledger's width is fractional, and the two rules
    // must leave no width where both labels, or neither, are in sight.
    for (let width = 300; width <= 1600; width += 0.5) {
      const fullGone = outOfSightAt(width).includes('.ledger .intensity-label')
      const shortShown = !hiddenAt(width).includes('.ledger .intensity-label-short')
      expect(fullGone, `at ${width} px`).toBe(width <= 560)
      expect(shortShown, `at ${width} px`).toBe(fullGone)
      expect(hiddenAt(width), `at ${width} px`).not.toContain('.ledger .intensity-label')
    }
  })
})
