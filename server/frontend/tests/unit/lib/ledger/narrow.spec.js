import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { hiddenAt, parseNarrowRules } from './narrowRules.js'

// The ledger at narrow widths (docs/dev/assignment_ledger_and_card.md, section
// 3.3): a container query drops the Level column, then the samples, then the
// batch's badges, then the intensity's bar, and the level's chips show in the
// Tier cell only where the Level column is gone. jsdom evaluates no container
// query, so these read the rules the ledger loads and work out what each width
// hides.

const LEDGER = readFileSync(
  join(import.meta.dirname, '..', '..', '..', '..', 'src', 'lib', 'ledger', 'AssignmentLedger.vue'),
  'utf8'
)

describe('the narrow ledger', () => {
  // The rules apply only where the ledger loads them and names itself the
  // container they measure.
  it('is loaded by the ledger, which is the container it measures', () => {
    expect(LEDGER).toMatch(/^import '\.\/narrow\.css'$/m)
    expect(LEDGER).toMatch(/\.ledger \{[^}]*container: ledger \/ inline-size;/)
  })

  // A scoped rule weighs as much as `.ledger .x` and comes later in the
  // bundle, so one that sets the display of a class these rules hide would
  // keep it on screen at every width (the intensity's bar did).
  it("is not overridden by the ledger's own display of what it hides", () => {
    const style = LEDGER.slice(LEDGER.indexOf('<style'))
    for (const { selector } of parseNarrowRules()) {
      const cls = selector.split(' ').at(-1)
      const own = new RegExp(`(^|\\n)${cls.replace('.', '\\.')}\\s*\\{[^}]*display:`)
      expect(style, selector).not.toMatch(own)
    }
  })

  it('reads every rule it holds', () => {
    expect(parseNarrowRules()).toHaveLength(5)
  })

  it('drops the Level column first, then the samples, then the badges, then the bar', () => {
    const drops = parseNarrowRules()
      .filter((rule) => rule.maxWidth != null)
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
    expect(hiddenAt(1200)).toEqual(['.ledger .tier-level'])
    expect(hiddenAt(901)).toEqual(['.ledger .tier-level'])
    expect(hiddenAt(900)).toEqual(['.ledger .col-level'])
    expect(hiddenAt(850)).toEqual(['.ledger .col-level'])
    expect(hiddenAt(640)).toEqual([
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
})
