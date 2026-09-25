/**
 * The two notations an ionization mechanism is written in.
 *
 * Mascope stores and shows a mechanism in the standard adduct notation:
 * `[M+H]+`, `[M-H]-`, `[M+Br]-`, `[M+CH4N2O+H]+`, `[M]+.`. The terms inside
 * the brackets are what is added to or removed from the molecule M, and the
 * sign after them is the ion's own charge; `[M]+.` and `[M]-.` are electron
 * transfer. The terms are written in alphabetical order whichever order they
 * were typed in, so one mechanism has one spelling: `[M+H+CH4N2O]+` is
 * `[M+CH4N2O+H]+`.
 *
 * The legacy notation, `<operation><moiety><moiety charge>` (`+H+`, `-H+`,
 * `+Br-`, `+`), is still read: the server accepts it on input, a row written
 * before the standard existed holds it until the server's migration rewrites
 * it, and a run recorded then keeps it. Its trailing sign is the moiety's
 * charge, not the ion's: `-H+` is `[M-H]-`, and `-H-` is `[M-H]+`.
 *
 * This mirrors `mascope_tools.composition.mechanism_notation`, which the
 * server validates and stores with, and the two agree on every spelling.
 */

const FORMULA_TEXT = /^[A-Za-z0-9()[\]^]+$/
const SIGNED_TERMS = /^(?:[+-][^+-]+)+$/
const SIGNED_TERM = /([+-])([^+-]+)/g

/** The index of the parenthesis closing the one `text` starts with. */
function closingParen(text) {
  let depth = 0
  for (let index = 0; index < text.length; index++) {
    if (text[index] === '(') depth += 1
    else if (text[index] === ')') {
      depth -= 1
      if (depth === 0) return index
    }
  }
  return -1
}

const isDigit = (char) => char >= '0' && char <= '9'

/**
 * A legacy moiety's terms: each leading group that something follows, then
 * the rest as it is. `(CH4N2O)H` is `CH4N2O` and `H`; `(CH4N2O)2H` stays one.
 */
function splitMoiety(moiety) {
  const terms = []
  let rest = moiety
  while (rest.startsWith('(')) {
    const close = closingParen(rest)
    if (close < 0 || close === rest.length - 1) break
    const group = rest.slice(1, close)
    if (!group || isDigit(group[0]) || isDigit(rest[close + 1])) break
    terms.push(group)
    rest = rest.slice(close + 1)
  }
  if (rest) terms.push(rest)
  return terms
}

/** The legacy moiety of these terms: all but the last parenthesised. */
const joinTerms = (terms) =>
  terms
    .slice(0, -1)
    .map((term) => `(${term})`)
    .join('') + terms[terms.length - 1]

/**
 * The terms in the order a mechanism is written in: alphabetical. Each term is
 * first split as far as a legacy moiety splits it, so a term that opens with a
 * group reads as the terms it holds wherever it stands: `[M+K+(H2O)Na]+` and
 * `[M+(H2O)Na+K]+` are both `[M+H2O+K+Na]+`.
 */
function orderedTerms(terms) {
  const sameTerms = (a, b) => a.length === b.length && a.every((term, i) => term === b[i])
  let parts = [...terms]
  for (;;) {
    const split = parts.flatMap((term) => splitMoiety(term))
    if (sameTerms(split, parts)) return split.sort()
    parts = split
  }
}

/** Whether every `closing` in `text` closes an `opening` before it. */
function nests(text, opening, closing) {
  let depth = 0
  for (const char of text) {
    if (char === opening) depth += 1
    else if (char === closing && --depth < 0) return false
  }
  return depth === 0
}

function checkFormulaText(text) {
  if (!FORMULA_TEXT.test(text)) return `'${text}' is not a formula`
  if (!nests(text, '(', ')') || !nests(text, '[', ']')) {
    return `'${text}' has unbalanced brackets`
  }
  return null
}

function parseLegacy(notation) {
  if (notation === '+' || notation === '-') {
    return { addition: notation === '-', moiety: '', charge: notation === '+' ? 1 : -1 }
  }
  if (notation.length < 3 || !'+-'.includes(notation[0]) || !'+-'.includes(notation.at(-1))) {
    throw new Error("Write it as '[M+H]+', '[M-H]-', '[M+Br]-' or '[M]+.'")
  }
  const moiety = notation.slice(1, -1)
  const problem = checkFormulaText(moiety)
  if (problem) throw new Error(problem)
  const addition = notation[0] === '+'
  const moietyCharge = notation.at(-1) === '+' ? 1 : -1
  return {
    addition,
    moiety: joinTerms(orderedTerms(splitMoiety(moiety))),
    charge: addition ? moietyCharge : -moietyCharge
  }
}

function parseStandard(notation) {
  const radical = notation.endsWith('.')
  const body = radical ? notation.slice(0, -1) : notation
  if (!body.startsWith('[M')) throw new Error("An ion is written around one molecule, '[M'")
  if (body.length < 4 || body.at(-2) !== ']' || !'+-'.includes(body.at(-1))) {
    throw new Error("It must end in ']+' or ']-', the charge of a singly charged ion")
  }
  const charge = body.at(-1) === '+' ? 1 : -1
  const inside = body.slice(2, -2)
  if (!inside) {
    if (!radical) throw new Error(`Electron transfer is written '${body}.'`)
    return { addition: charge < 0, moiety: '', charge }
  }
  if (radical) throw new Error("Only electron transfer, '[M]+.' or '[M]-.', carries the dot")
  if (!SIGNED_TERMS.test(inside)) {
    throw new Error("Each term is added with '+' or removed with '-', as in '[M-H]-'")
  }
  const signed = [...inside.matchAll(SIGNED_TERM)]
  const operations = new Set(signed.map((match) => match[1]))
  if (operations.size > 1) throw new Error('A mechanism adds its terms or removes them, not both')
  const terms = signed.map((match) => match[2])
  for (const term of terms) {
    const problem = checkFormulaText(term)
    if (problem) throw new Error(problem)
    if (isDigit(term[0])) throw new Error(`Write '${term}' as a formula, '(H2O)2' not '2H2O'`)
  }
  return { addition: operations.has('+'), moiety: joinTerms(orderedTerms(terms)), charge }
}

/**
 * Read a mechanism written in either notation.
 *
 * @param {string} text - `'[M-H]-'` or `'-H+'`
 * @returns {{addition: boolean, moiety: string, charge: number}} whether the
 *   moiety is added, the moiety as the legacy notation writes it (empty for
 *   electron transfer), and the ion's charge
 * @throws {Error} naming what is wrong, when the text is neither notation
 */
export function parseMechanism(text) {
  const notation = String(text ?? '').trim()
  return notation.startsWith('[') ? parseStandard(notation) : parseLegacy(notation)
}

/**
 * Why a mechanism does not read, for a form to say; null when it does.
 *
 * @param {string} text - The mechanism as typed.
 * @returns {string|null}
 */
export function mechanismProblem(text) {
  try {
    parseMechanism(text)
    return null
  } catch (error) {
    return error.message
  }
}

/**
 * A mechanism in the standard adduct notation, from either notation. Text
 * that reads as neither comes back as it was given, so a stored row the rules
 * refuse is still shown for what it is.
 *
 * @param {string|null|undefined} text - The mechanism.
 * @returns {string} `'[M-H]-'` for `'-H+'`; empty for no mechanism.
 */
export function standardMechanism(text) {
  if (text == null) return ''
  let parts
  try {
    parts = parseMechanism(text)
  } catch {
    return String(text).trim()
  }
  const polarity = parts.charge > 0 ? '+' : '-'
  if (!parts.moiety) return `[M]${polarity}.`
  const operation = parts.addition ? '+' : '-'
  const inside = splitMoiety(parts.moiety)
    .map((term) => operation + term)
    .join('')
  return `[M${inside}]${polarity}`
}

/**
 * The terms a mechanism adds or removes, as the standard notation writes them:
 * `['CH4N2O', 'H']` for `[M+CH4N2O+H]+` or `+(CH4N2O)H+`, none for electron
 * transfer.
 *
 * @param {string} text - The mechanism, in either notation.
 * @returns {string[]}
 * @throws {Error} naming what is wrong, when the text is neither notation
 */
export function mechanismTerms(text) {
  const { moiety } = parseMechanism(text)
  return moiety ? splitMoiety(moiety) : []
}

/**
 * The polarity of the ion a mechanism makes, or null when it does not read.
 *
 * @param {string} text - The mechanism, in either notation.
 * @returns {'+'|'-'|null}
 */
export function mechanismPolarity(text) {
  try {
    return parseMechanism(text).charge > 0 ? '+' : '-'
  } catch {
    return null
  }
}
