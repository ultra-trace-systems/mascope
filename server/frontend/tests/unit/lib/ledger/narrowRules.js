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

// `prop: value; ...` as an object, in the order written.
const declarationsOf = (body) =>
  Object.fromEntries(
    body
      .split(';')
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const at = part.indexOf(':')
        return [part.slice(0, at).trim(), part.slice(at + 1).trim()]
      })
  )

// The visually-hidden recipe, whole. The clip takes the element from sight;
// the absolute 1 px box takes it out of the layout, so the column it sat in
// narrows. A rule that clips but keeps the element's box hides it and narrows
// nothing, so it does not count as taking it from sight.
const OUT_OF_SIGHT = {
  position: 'absolute',
  width: '1px',
  height: '1px',
  overflow: 'hidden',
  'clip-path': 'inset(50%)',
  'white-space': 'nowrap'
}

// What a rule does to what it selects: `drop` takes it out of the layout
// (display: none), `sight` takes it from sight but leaves it on the page and in
// the accessibility tree (the whole recipe above), `size` changes its size
// alone.
const effectOf = (declarations) => {
  if (declarations.display === 'none') return 'drop'
  if (Object.entries(OUT_OF_SIGHT).every(([prop, value]) => declarations[prop] === value)) {
    return 'sight'
  }
  return 'size'
}

// Every `selectors { declarations }` in `css`, a selector list split up.
function* rulesIn(css) {
  for (const [, selectors, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const declarations = declarationsOf(body)
    for (const selector of selectors.split(',')) {
      yield { selector: selector.trim(), declarations, effect: effectOf(declarations) }
    }
  }
}

/**
 * The narrow rules as `{ minWidth, maxWidth, selector, declarations, effect }`,
 * each applying to one selector at the ledger widths its container query
 * names (one bound or the other). A rule outside any query, which applies at
 * every width, has neither bound.
 */
export function parseNarrowRules(css = rules) {
  const plain = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const found = []
  // `(min-width: N)`, `(max-width: N)`, or `(width > N)`, which holds above
  // N and so leaves no gap beside a `(max-width: N)` at a fractional width.
  const query =
    /@container\s+ledger\s*\((?:(min|max)-width:|width\s*(>))\s*(\d+)px\)\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}/g
  for (const [, bound, above, width, body] of plain.matchAll(query)) {
    for (const rule of rulesIn(body)) {
      found.push({
        minWidth: bound === 'min' || above ? Number(width) : null,
        maxWidth: bound === 'max' ? Number(width) : null,
        above: Boolean(above),
        ...rule
      })
    }
  }
  for (const rule of rulesIn(plain.replace(query, ''))) {
    found.push({ minWidth: null, maxWidth: null, ...rule })
  }
  return found
}

const appliesAt = (rule, width) =>
  (rule.maxWidth == null || width <= rule.maxWidth) &&
  (rule.minWidth == null || (rule.above ? width > rule.minWidth : width >= rule.minWidth))

const rulesAt = (width, css) => parseNarrowRules(css).filter((rule) => appliesAt(rule, width))

/** The selectors dropped (display: none) in a ledger of `width` px, in the file's order. */
export const hiddenAt = (width, css = rules) =>
  rulesAt(width, css)
    .filter((rule) => rule.effect === 'drop')
    .map((rule) => rule.selector)

/** The selectors taken from sight, but left on the page, in a ledger of `width` px. */
export const outOfSightAt = (width, css = rules) =>
  rulesAt(width, css)
    .filter((rule) => rule.effect === 'sight')
    .map((rule) => rule.selector)

/**
 * The value the narrow rules give `property` on `selector` in a ledger of
 * `width` px, or undefined: the last rule that sets it, as the cascade picks
 * between rules of one weight. The plain rules are read first, as the file has
 * them at its top, and the query rules after, so a query's rule overrides the
 * plain one it lowers.
 */
export const styleAt = (selector, property, width, css = rules) => {
  const all = parseNarrowRules(css)
  const plainFirst = [
    ...all.filter((rule) => rule.minWidth == null && rule.maxWidth == null),
    ...all.filter((rule) => rule.minWidth != null || rule.maxWidth != null)
  ]
  return plainFirst
    .filter((rule) => rule.selector === selector && appliesAt(rule, width))
    .map((rule) => rule.declarations[property])
    .filter((value) => value != null)
    .at(-1)
}

/**
 * Whether `element` shows in a ledger of `width` px: no ancestor of it, itself
 * included, matches a selector the rules drop at that width.
 */
export const shownAt = (element, width, css = rules) =>
  !hiddenAt(width, css).some((selector) => element.closest(selector))

/**
 * Whether `element` is in sight in a ledger of `width` px: shown, and no
 * ancestor of it, itself included, taken from sight.
 */
export const seenAt = (element, width, css = rules) =>
  shownAt(element, width, css) &&
  !outOfSightAt(width, css).some((selector) => element.closest(selector))
