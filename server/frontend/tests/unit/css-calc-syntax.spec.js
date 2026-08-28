import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// CSS requires whitespace around + and - inside calc(): `calc(100% -10rem)`
// parses as a length followed by a negative length, which is not an expression,
// so the whole declaration is dropped - silently, with no console warning and
// no visible error. One of those sat in the dashboard's stylesheet for long
// enough that the layout it was meant to produce was never the one anyone saw.
// * and / need no spaces, and a leading sign is a signed operand, not an
// operator, so only binary + and - are checked here.

const SRC = join(import.meta.dirname, '..', '..', 'src')

/** Every file under src/ that can carry CSS. */
function styleSources(dir) {
  const found = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) found.push(...styleSources(path))
    else if (/\.(vue|css)$/.test(entry.name)) found.push(path)
  }
  return found
}

/** Each calc(...) in `source`, brackets balanced, as { text, line }. */
function calcExpressions(source) {
  const found = []
  for (let start = source.indexOf('calc('); start !== -1; start = source.indexOf('calc(', start)) {
    let depth = 0
    let end = start + 'calc'.length
    for (; end < source.length; end++) {
      if (source[end] === '(') depth++
      else if (source[end] === ')' && --depth === 0) break
    }
    found.push({
      text: source.slice(start, end + 1),
      line: source.slice(0, start).split('\n').length
    })
    start = end + 1
  }
  return found
}

/** True when every binary + and - in the expression is surrounded by spaces. */
function operatorsAreSpaced(expression) {
  // Custom property names are full of dashes that are not operators.
  const body = expression.replace(/--[\w-]+/g, 'x')
  for (let i = 0; i < body.length; i++) {
    if (body[i] !== '+' && body[i] !== '-') continue
    const before = body.slice(0, i)
    const operand = before.trimEnd().slice(-1)
    // Nothing (or another operator) to the left means a signed operand.
    if (!operand || '(*/,+-'.includes(operand)) continue
    if (!/\s$/.test(before) || !/^\s/.test(body.slice(i + 1))) return false
  }
  return true
}

describe('calc() expressions parse', () => {
  const offenders = styleSources(SRC).flatMap((path) =>
    calcExpressions(readFileSync(path, 'utf8'))
      .filter(({ text }) => !operatorsAreSpaced(text))
      .map(({ text, line }) => `${path.slice(SRC.length + 1)}:${line}  ${text}`)
  )

  it('have whitespace around every + and -, or the browser drops them', () => {
    expect(offenders).toEqual([])
  })
})
