// Verification (labelling) vocabulary shared by the capture control, the verdict
// badge, and the ledger filter. Pure metadata -- no store state. Mirrors the
// backend Verdict / EvidenceLevel literals (peak_assignments/schemas.py).

import { isValidInchikey } from '@/lib/chem'

// Schymanski-aligned evidence levels, strongest -> weakest. Required when a
// verdict is `confirmed` (enforced server-side too).
export const EVIDENCE_LEVELS = [
  { value: 'reference_standard', label: 'Reference standard' },
  { value: 'msms', label: 'MS/MS' },
  { value: 'orthogonal', label: 'Orthogonal' },
  { value: 'pattern', label: 'Isotope/adduct pattern' },
  { value: 'visual', label: 'Visual' }
]
const EVIDENCE_LABEL = Object.fromEntries(EVIDENCE_LEVELS.map((e) => [e.value, e.label]))
export const evidenceLabel = (key) => (key ? (EVIDENCE_LABEL[key] ?? key) : null)

// verdict -> chip metadata (label, phosphor icon, PrimeVue Tag severity). Confirm
// and Reject are given equal weight by design (reject is a first-class label).
export const VERDICT_META = {
  confirmed: { label: 'Confirmed', icon: 'ph ph-check-circle', severity: 'success' },
  rejected: { label: 'Rejected', icon: 'ph ph-x-circle', severity: 'danger' },
  unsure: { label: 'Unsure', icon: 'ph ph-question', severity: 'warn' }
}

// --- Identification claims ---------------------------------------------------
// A claim is a confirmation that names a structure, an InChIKey and its name,
// or at 3b a class (docs/dev/identification_claims.md section 5). The server
// fixes the level it states, its number and the shared scale's letter, from
// the evidence and what the person says it showed (mapping version 2); these
// mirror its rules so the form can say them before a request
// (peak_assignments/claims.py).

// Evidence that is a statement about a structure, or at 3b about a class.
export const STRUCTURE_EVIDENCE = ['reference_standard', 'msms', 'orthogonal']

// What a reference standard is under the mapping, said where it is picked.
export const STANDARD_MEANS =
  'An authentic standard on this instrument with the same reagent chemistry'

// The line beside a standard that takes a claim to level 1.
export const INDEPENDENT_LINES = [
  { value: 'msms', label: 'MS/MS' },
  { value: 'separation', label: 'A separation' },
  { value: 'instrument', label: 'Another instrument' }
]

const LIFT_TO_ONE =
  'an authentic standard on this instrument with the same reagent chemistry, and an independent line'
const LIFT_TO_TWO = 'a library spectrum, or two or more fragments that fit one structure'

// evidence level -> what its evidence may have shown, each with the level it
// states and what it names: a structure, a class (3b, by name alone) or a
// formula. The first pattern option is the one the server takes when none is
// sent; MS/MS and orthogonal confirmations must say.
export const EVIDENCE_DETAILS = {
  msms: [
    {
      value: 'library',
      label: 'A library spectrum match',
      level: 2,
      letter: 'a',
      names: 'structure',
      lift: LIFT_TO_ONE
    },
    {
      value: 'fragments',
      label: 'Two or more fragments that fit one structure',
      level: 2,
      letter: 'b',
      names: 'structure',
      lift: LIFT_TO_ONE
    },
    {
      value: 'loss',
      label: 'One loss: a class',
      level: 3,
      letter: 'b',
      names: 'class',
      lift: LIFT_TO_TWO
    }
  ],
  orthogonal: [
    {
      value: 'separation',
      label: 'A separation that leaves one structure',
      level: 2,
      letter: 'b',
      names: 'structure',
      lift: LIFT_TO_ONE
    },
    {
      value: 'data_and_list',
      label: 'The data and a list agree',
      level: 3,
      letter: 'a',
      names: 'structure',
      lift: LIFT_TO_TWO
    },
    { value: 'class', label: 'A class', level: 3, letter: 'b', names: 'class', lift: LIFT_TO_TWO },
    {
      value: 'context_list',
      label: 'A context list',
      level: 3,
      letter: 'c',
      names: 'structure',
      lift: LIFT_TO_TWO
    },
    {
      value: 'homologue',
      label: 'A homologue series',
      level: 3,
      letter: 'd',
      names: 'structure',
      lift: LIFT_TO_TWO
    }
  ],
  pattern: [
    {
      value: 'split_open',
      label: 'Which molecule and reagent make the ion is open',
      level: 4,
      letter: 'b',
      names: 'formula',
      lift: 'a pinned molecule-reagent split'
    },
    {
      value: 'split_pinned',
      label: 'Which molecule and reagent make the ion is pinned',
      level: 4,
      letter: 'a',
      names: 'formula',
      lift: "a context list for the run's chemistry, or one MS/MS loss"
    }
  ]
}

// The evidence levels whose confirmation must say what it showed.
export const DETAIL_REQUIRED = ['msms', 'orthogonal']

/**
 * What a confirmation states, as the server will fix it: the level, its
 * letter, what it names and its lift. A reference standard states 1 with an
 * independent line and 2b without, its lift "an independent line"; a pattern
 * that says nothing of the split states it open (4b). None on a visual look,
 * or before an MS/MS or orthogonal confirmation says what it showed.
 *
 * @param {?string} evidence - the evidence level
 * @param {?string} [detail] - what the evidence showed
 * @param {?string} [line] - a reference standard's independent line
 * @returns {?{level: number, letter: ?string, label: string, names: string, lift: ?string}}
 */
export function claimStatement(evidence, detail = null, line = null) {
  if (evidence === 'reference_standard') {
    return line
      ? { level: 1, letter: null, label: '1', names: 'structure', lift: null }
      : { level: 2, letter: 'b', label: '2b', names: 'structure', lift: 'an independent line' }
  }
  const options = EVIDENCE_DETAILS[evidence]
  if (!options) return null
  const found = options.find(
    (option) => option.value === (detail ?? (evidence === 'pattern' ? 'split_open' : null))
  )
  if (!found) return null
  const { level, letter, names, lift } = found
  return { level, letter, label: `${level}${letter ?? ''}`, names, lift }
}

/** Whether the statement names a structure, which the claim must then give. */
export const needsStructure = (evidence, detail = null, line = null) =>
  claimStatement(evidence, detail, line)?.names === 'structure'

/** Whether the statement is a class claim (3b): a class by name, no InChIKey. */
export const isClassClaim = (evidence, detail = null) =>
  claimStatement(evidence, detail)?.names === 'class'

/**
 * A level as the badge writes it: `Level 2b`, or `Level 2` for a claim made
 * under version 1 of the mapping, which has no letter.
 *
 * @param {?number} level
 * @param {?string} [letter]
 * @returns {?string}
 */
export const levelLabel = (level, letter = null) =>
  Number.isInteger(level) ? `Level ${level}${letter ?? ''}` : null

/**
 * The statement a recorded verdict's level was fixed from, as its context
 * keeps it: what the evidence showed, the independent line, the evidence line
 * and the lift. Null on a verdict that states no level, or one recorded under
 * version 1.
 *
 * @param {?object} record - a verdict record
 * @returns {?{evidence_detail: ?string, independent_line: ?string, evidence: ?string, lift: ?string}}
 */
export const claimOf = (record) => {
  const claim = record?.context?.claim
  return claim && typeof claim === 'object' ? claim : null
}

const pad = (n) => String(n).padStart(2, '0')

/**
 * An ISO time as a `datetime-local` input's value, in the reader's zone, to
 * the second.
 *
 * @param {?string} iso
 * @returns {string} '' for none
 */
export function toLocalInput(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

/**
 * A `datetime-local` input's value as an ISO time in UTC. The end of a
 * window takes its last second whole, so a sample acquired part way through
 * it is inside.
 *
 * @param {?string} value
 * @param {object} [options]
 * @param {boolean} [options.end] - the window's last time
 * @returns {?string}
 */
export function fromLocalInput(value, { end = false } = {}) {
  if (!value) return null
  const time = new Date(value).getTime()
  if (Number.isNaN(time)) return null
  return new Date(time + (end ? 999 : 0)).toISOString()
}

/**
 * The first and last acquisition time of a batch's samples, for a window to
 * start from.
 *
 * @param {?Array<{datetime_utc: ?string}>} samples
 * @returns {?{first: string, last: string}}
 */
export function acquisitionRange(samples) {
  const times = (Array.isArray(samples) ? samples : [])
    .map((sample) => sample?.datetime_utc)
    .filter((time) => typeof time === 'string' && !Number.isNaN(new Date(time).getTime()))
    .sort((a, b) => new Date(a) - new Date(b))
  return times.length ? { first: times[0], last: times[times.length - 1] } : null
}

/**
 * A level-1 batch claim's window, in the reader's local time.
 *
 * @param {?object} record - a batch verdict record
 * @returns {?string} `from <first> to <last>`, or null without a window
 */
export function windowText(record) {
  if (!record?.window_start_utc || !record?.window_end_utc) return null
  const when = (value) => new Date(value).toLocaleString()
  return `from ${when(record.window_start_utc)} to ${when(record.window_end_utc)}`
}

/**
 * The structures a confirmation may name: the row's candidates, the target's
 * declaration first, then the reference identities, each InChIKey once. An
 * identity without an InChIKey names no structure and is left out, and so is
 * one whose key is not an InChIKey's shape: a list's key is only fitted to
 * length at ingest, and the server refuses a claim on such a key.
 *
 * @param {object} [candidates]
 * @param {?{name: ?string, inchikey: ?string}} [candidates.target] - what the
 *   matched target compound declares
 * @param {Array<object>} [candidates.identities] - the reference identities,
 *   each with `name`, `source` and `inchikey`
 * @returns {Array<{inchikey: string, name: ?string, label: string}>}
 */
export function claimCandidates({ target = null, identities = [] } = {}) {
  const found = []
  const add = (record, label) => {
    const inchikey = typeof record?.inchikey === 'string' ? record.inchikey.trim() : ''
    if (!inchikey || !isValidInchikey(inchikey)) return
    if (found.some((candidate) => candidate.inchikey === inchikey)) return
    const name = typeof record?.name === 'string' && record.name ? record.name : null
    found.push({ inchikey, name, label })
  }
  add(target, 'target')
  for (const identity of Array.isArray(identities) ? identities : []) {
    add(identity, identity?.source || 'reference list')
  }
  return found
}
