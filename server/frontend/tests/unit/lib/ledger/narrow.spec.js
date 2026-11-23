import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// Read as text: the test run compiles no CSS.
const rules = readFileSync(
  join(import.meta.dirname, '..', '..', '..', '..', 'src', 'lib', 'ledger', 'narrow.css'),
  'utf8'
)

// The ledger at narrow widths (docs/dev/assignment_ledger_and_card.md, section
// 3.3): a container query drops the level, then the samples, then the batch's
// badges, then the intensity's bar. jsdom evaluates no container query, so
// these read the rules the ledger loads and work out what each width hides.

/** The rules as `{ maxWidth, selector }`, each hiding one selector. */
function parse(css) {
  const found = []
  const query = /@container\s+ledger\s*\(max-width:\s*(\d+)px\)\s*\{([^@]*?\})\s*\}/g
  for (const [, width, body] of css.matchAll(query)) {
    for (const [, selector] of body.matchAll(/([^{}]+)\{\s*display:\s*none;\s*\}/g)) {
      found.push({ maxWidth: Number(width), selector: selector.trim() })
    }
  }
  return found
}

/** The cells hidden in a ledger of `width` px. */
const hiddenAt = (width) =>
  parse(rules)
    .filter((rule) => width <= rule.maxWidth)
    .map((rule) => rule.selector)

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

  it('names the ledger as the container it measures', () => {
    expect(parse(rules)).toHaveLength(4)
  })

  it('drops the level first, then the samples, then the badges, then the bar', () => {
    const byWidth = [...parse(rules)].sort((a, b) => b.maxWidth - a.maxWidth)
    expect(byWidth.map((rule) => rule.selector)).toEqual([
      '.ledger .col-level',
      '.ledger .col-samples',
      '.ledger .col-across',
      '.ledger .intensity-bar'
    ])
  })

  it('keeps every cell at a wide width, and drops them in order as it narrows', () => {
    expect(hiddenAt(1200)).toEqual([])
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
