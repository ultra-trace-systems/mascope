import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// The ledger's narrow rules (src/lib/ledger/narrow.css), read as text: the
// test run compiles no CSS and jsdom evaluates no container query. Shared by
// narrow.spec.js, which reads the rules, and assignmentLedger.spec.js, which
// applies them to a mounted ledger to see what a row shows at a width.

const rules = readFileSync(
  join(import.meta.dirname, '..', '..', '..', '..', 'src', 'lib', 'ledger', 'narrow.css'),
  'utf8'
)

/**
 * The rules as `{ minWidth, maxWidth, selector }`, each hiding one selector
 * at the ledger widths its container query names (one bound or the other).
 */
export function parseNarrowRules(css = rules) {
  const found = []
  const query = /@container\s+ledger\s*\((min|max)-width:\s*(\d+)px\)\s*\{([^@]*?\})\s*\}/g
  for (const [, bound, width, body] of css.matchAll(query)) {
    for (const [, selector] of body.matchAll(/([^{}]+)\{\s*display:\s*none;\s*\}/g)) {
      found.push({
        minWidth: bound === 'min' ? Number(width) : null,
        maxWidth: bound === 'max' ? Number(width) : null,
        selector: selector.trim()
      })
    }
  }
  return found
}

/** The selectors hidden in a ledger of `width` px, in the file's order. */
export const hiddenAt = (width, css = rules) =>
  parseNarrowRules(css)
    .filter(
      (rule) =>
        (rule.maxWidth == null || width <= rule.maxWidth) &&
        (rule.minWidth == null || width >= rule.minWidth)
    )
    .map((rule) => rule.selector)

/**
 * Whether `element` shows in a ledger of `width` px: no ancestor of it, itself
 * included, matches a selector the rules hide at that width.
 */
export const shownAt = (element, width, css = rules) =>
  !hiddenAt(width, css).some((selector) => element.closest(selector))
