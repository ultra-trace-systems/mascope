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
 * Three shapes only the standard notation says, and only it writes: a
 * multimer, `[2M+H]+`; a charge above one, `[M+2H]2+` or `[M+Ca]2+`, where a
 * coefficient is written on the one term and is the ion's charge (`2H` is
 * the formula `(H)2`); and a neutral loss, `[M+H-H2O]+`, a term removed from
 * a mechanism that adds. They have no legacy spelling. One species written as
 * many times as the charge is folded into the coefficient (`[M+H+H]2+` is
 * `[M+2H]2+`); a species of one charge alone on a higher one (`[M+H]2+`) or
 * repeated in its formula (`[M+H2]2+`) is refused, and so is a removed proton
 * in a mechanism that adds (`[M+H2O-H]-`). Only a mechanism that adds has a
 * neutral loss.
 *
 * A mechanism is also a row (`mechanismRow`): whether its species is added
 * or removed, the species and its charge, how many molecules the ion holds,
 * the neutral it loses, and what follows from those - the ion's charge and
 * polarity.
 *
 * This mirrors `mascope_tools.composition.mechanism_notation`, which the
 * server validates and stores with, and the two agree on every spelling.
 */

const FORMULA_TEXT = /^[A-Za-z0-9()[\]^]+$/
const SIGNED_TERMS = /^(?:[+-][^+-]+)+$/
const SIGNED_TERM = /([+-])([^+-]+)/g
// The head of a standard spelling, `[M` or `[2M`, and its tail, the radical
// dot taken off: the closing bracket, the charge's size, its sign.
const STANDARD_HEAD = /^\[(\d+)?M/
const STANDARD_TAIL = /\](\d+)?([+-])$/
const COEFFICIENT_TERM = /^(\d+)(.*)$/s
// One element repeated, `H2` or `Na2`: the element and its count.
const REPEATED_ELEMENT = /^([A-Z][a-z]?)(\d+)$/
// The species that carry one charge each, never more. One of them alone on an
// ion of a higher charge is a coefficient left out (`[M+H]2+`), and the same
// element repeated as a formula is the coefficient written as one (`[M+H2]2+`).
const MONOVALENT_SPECIES = new Set([
  'H',
  'Li',
  'Na',
  'K',
  'Rb',
  'Cs',
  'F',
  'Cl',
  'Br',
  'I',
  'NH4',
  'NO3',
  'Ag'
])

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

/** What a moiety written `(X)k` multiplies, and by how much; null otherwise. */
function multipliedGroup(moiety) {
  if (!moiety.startsWith('(')) return null
  const close = closingParen(moiety)
  if (close < 2) return null
  const count = moiety.slice(close + 1)
  if (!/^[1-9]\d*$/.test(count)) return null
  return [moiety.slice(1, close), Number(count)]
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
    return {
      addition: notation === '-',
      moiety: '',
      charge: notation === '+' ? 1 : -1,
      multimer: 1,
      neutralLoss: ''
    }
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
    charge: addition ? moietyCharge : -moietyCharge,
    multimer: 1,
    neutralLoss: ''
  }
}

/** A molecule count or a charge's size as written: 1 where there is none. */
function count(text, what) {
  if (text === undefined) return 1
  if (text[0] === '0' || Number(text) < 2) throw new Error(`${what}, with no count`)
  return Number(text)
}

/** A term as a formula: a coefficient, `2H`, is `(H)2`, only as the ion's charge. */
function addedFormula(term, size, alone) {
  if (!isDigit(term[0])) return term
  if (size === 1) throw new Error(`Write '${term}' as a formula, '(H2O)2' not '2H2O'`)
  if (!alone) {
    throw new Error("A coefficient goes on a mechanism's only term; write '[M+H+H+Na]3+'")
  }
  const [, coefficient, species] = term.match(COEFFICIENT_TERM)
  if (!species || coefficient[0] === '0' || Number(coefficient) !== size) {
    throw new Error(`The coefficient of '${term}' is the ion's charge, as in '[M+2H]2+'`)
  }
  return `(${species})${coefficient}`
}

/**
 * The terms of an ion of charge `size`, one species repeated as many times as
 * the charge folded into the group it multiplies: `[M+H+H]2+` is `(H)2`, the
 * moiety of `[M+2H]2+`. Any other terms as they are: `[M+H+H+Na]3+` keeps three.
 */
function foldedTerms(terms, size) {
  if (size === 1) return terms
  const ordered = orderedTerms(terms)
  if (ordered.length !== size || new Set(ordered).size !== 1 || isDigit(ordered[0][0])) {
    return terms
  }
  return [`(${ordered[0]})${size}`]
}

/**
 * Refuse one species of a single charge written as carrying the ion's whole
 * charge, `[M+H]2+`, or with the coefficient written into its formula,
 * `[M+H2]2+`: both mean `[M+2H]2+`.
 */
function checkSingleCharges(terms, parts) {
  const size = Math.abs(parts.charge)
  if (size === 1 || terms.length !== 1) return
  const [term] = terms
  const repeated = term.match(REPEATED_ELEMENT)
  let species, reading
  if (MONOVALENT_SPECIES.has(term)) {
    species = term
    reading = `One ${term} carries one charge, not ${size}`
  } else if (repeated && MONOVALENT_SPECIES.has(repeated[1]) && Number(repeated[2]) === size) {
    species = repeated[1]
    reading = `'${term}' is ${size} ${species}, which carry one charge each`
  } else {
    return
  }
  const meant = writeStandard({ ...parts, moiety: `(${species})${size}` })
  throw new Error(`${reading}; write '${meant}'`)
}

function parseStandard(notation) {
  const radical = notation.endsWith('.')
  const body = radical ? notation.slice(0, -1) : notation
  const head = body.match(STANDARD_HEAD)
  if (!head) throw new Error("An ion is written around its molecule, '[M' or '[2M'")
  const tail = body.match(STANDARD_TAIL)
  if (!tail || tail.index < head[0].length) {
    throw new Error("It must end in the ion's charge, ']+', ']-' or ']2+'")
  }
  const multimer = count(head[1], "One molecule is written '[M'")
  const size = count(tail[1], "A single charge is written ']+' or ']-'")
  const charge = tail[2] === '+' ? size : -size
  const inside = body.slice(head[0].length, tail.index)
  if (!inside) {
    if (!radical) throw new Error(`Electron transfer is written '${body}.'`)
    if (size > 1) throw new Error("Electron transfer moves one electron, '[M]+.' or '[M]-.'")
    return { addition: charge < 0, moiety: '', charge, multimer, neutralLoss: '' }
  }
  if (radical) throw new Error("Only electron transfer, '[M]+.' or '[M]-.', carries the dot")
  if (!SIGNED_TERMS.test(inside)) {
    throw new Error("Each term is added with '+' or removed with '-', as in '[M-H]-'")
  }
  const signed = [...inside.matchAll(SIGNED_TERM)]
  const added = signed.filter((match) => match[1] === '+').map((match) => match[2])
  const removed = signed.filter((match) => match[1] === '-').map((match) => match[2])
  // A mechanism that adds and removes adds what carries the charge and loses
  // a neutral: [M+H-H2O]+.
  const [terms, lost] = added.length ? [added, removed] : [removed, []]
  for (const term of [...terms, ...lost]) {
    const problem = checkFormulaText(term)
    if (problem) throw new Error(problem)
  }
  for (const term of lost) {
    if (isDigit(term[0])) {
      throw new Error(`Write the neutral loss '${term}' as a formula, '(H2O)2' not '2H2O'`)
    }
  }
  // A removed proton carries a charge, so it is never the neutral loss:
  // [M+H2O-H]- and [M+Na-H-H]- say an ion the row has no way to write.
  if (lost.length && orderedTerms(lost).includes('H')) {
    throw new Error("A removed 'H' is a proton, not a neutral loss, as 'H2O' is in '[M+H-H2O]+'")
  }
  const folded = foldedTerms(terms, size)
  const formulas = folded.map((term) => addedFormula(term, size, folded.length === 1))
  const parts = {
    addition: added.length > 0,
    moiety: joinTerms(orderedTerms(formulas)),
    charge,
    multimer,
    neutralLoss: lost.length ? joinTerms(orderedTerms(lost)) : ''
  }
  checkSingleCharges(folded, parts)
  return parts
}

/**
 * Read a mechanism written in either notation.
 *
 * @param {string} text - `'[M-H]-'` or `'-H+'`
 * @returns {{addition: boolean, moiety: string, charge: number, multimer: number,
 *   neutralLoss: string}} whether the moiety is added, the moiety as the legacy
 *   notation writes it (empty for electron transfer), the ion's charge, how many
 *   molecules it holds, and the neutral it loses (empty for none)
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
  return writeStandard(parts)
}

/** The display terms of a moiety: `2H` for `(H)2` on an ion of charge two. */
function displayTerms(parts) {
  const size = Math.abs(parts.charge)
  const multiplied = size > 1 ? multipliedGroup(parts.moiety) : null
  if (multiplied && multiplied[1] === size) return [`${size}${multiplied[0]}`]
  return splitMoiety(parts.moiety)
}

function writeStandard(parts) {
  const molecule = parts.multimer > 1 ? `${parts.multimer}M` : 'M'
  const size = Math.abs(parts.charge)
  const charge = (size > 1 ? String(size) : '') + (parts.charge > 0 ? '+' : '-')
  if (!parts.moiety) return `[${molecule}]${charge}.`
  const operation = parts.addition ? '+' : '-'
  const lost = parts.neutralLoss ? splitMoiety(parts.neutralLoss) : []
  const inside =
    displayTerms(parts)
      .map((term) => operation + term)
      .join('') + lost.map((term) => `-${term}`).join('')
  return `[${molecule}${inside}]${charge}`
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
  const parts = parseMechanism(text)
  return parts.moiety ? displayTerms(parts) : []
}

/**
 * Every formula a mechanism adds, removes or loses: its terms as formulas
 * (`(H)2` where the notation writes `2H`), then its neutral loss's. What a
 * form checks for real elements.
 *
 * @param {string} text - The mechanism, in either notation.
 * @returns {string[]}
 * @throws {Error} naming what is wrong, when the text is neither notation
 */
export function mechanismFormulas(text) {
  const { moiety, neutralLoss } = parseMechanism(text)
  return [...(moiety ? splitMoiety(moiety) : []), ...(neutralLoss ? splitMoiety(neutralLoss) : [])]
}

/**
 * A mechanism as the server's structured row, with what follows from it, or
 * null when it does not read.
 *
 * @param {string} text - The mechanism, in either notation.
 * @returns {?{operation: 'add'|'remove', species: string, speciesCharge: number,
 *   multimerN: number, neutralLoss: ?string, ionCharge: number,
 *   polarity: '+'|'-'}}
 */
export function mechanismRow(text) {
  let parts
  try {
    parts = parseMechanism(text)
  } catch {
    return null
  }
  return {
    operation: parts.addition ? 'add' : 'remove',
    species: parts.moiety,
    speciesCharge: parts.addition ? parts.charge : -parts.charge,
    multimerN: parts.multimer,
    neutralLoss: parts.neutralLoss || null,
    ionCharge: parts.charge,
    polarity: parts.charge > 0 ? '+' : '-'
  }
}

/**
 * The ion a mechanism makes, in words, for a form to show: its charge, and
 * the molecules and the neutral loss where the mechanism has them.
 *
 * @param {string} text - The mechanism, in either notation.
 * @returns {string} `'Ion charge 2+ · 2 molecules · loses H2O'`; empty when it
 *   does not read.
 */
export function describeMechanism(text) {
  const row = mechanismRow(text)
  if (!row) return ''
  const size = Math.abs(row.ionCharge)
  const parts = [`Ion charge ${size > 1 ? size : ''}${row.polarity}`]
  if (row.multimerN > 1) parts.push(`${row.multimerN} molecules`)
  if (row.neutralLoss) parts.push(`loses ${splitMoiety(row.neutralLoss).join(' and ')}`)
  return parts.join(' · ')
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
