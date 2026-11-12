/**
 * What the batch's isotope checks state of a batch peak, in words.
 *
 * "Read batch checks" (step 4.2g of the assignment plan, `batch_checks.py`)
 * reads three facts only a batch can state of a batch peak's reading, and the
 * batch peak carries them as `batch_checks` (a sample's row as the same key):
 *
 * - `halogen_lock`: a chlorine or bromine ion's 37Cl or 81Br line, stamped by
 *   "Stamp isotopologues", sitting at the exact spacing from it - `locked`,
 *   `silicon` where the 29Si line says the line is a 30Si one, `not_locked` or
 *   `untestable`;
 * - `label_twin`: a labelled ion's 14N twin one unit down at the label's
 *   impurity share - `present`, `absent` or `untestable` - and how far the
 *   14N line runs above that share (`excess`);
 * - `neutral_pair`: a uronium compound's protonated and urea-clustered peaks,
 *   as "Read co-variation" reads them - `established`, `not_established` or
 *   `untestable`.
 *
 * They are display-only: no tier reads them, and these words say so.
 */

export const LOCK_LOCKED = 'locked'
export const LOCK_SILICON = 'silicon'
export const LOCK_NOT_LOCKED = 'not_locked'
export const TWIN_PRESENT = 'present'
export const TWIN_ABSENT = 'absent'
export const PAIR_ESTABLISHED = 'established'
export const PAIR_NOT_ESTABLISHED = 'not_established'

/** The tones a fact's tag reads in: it holds, it refutes, or it cannot say. */
export const TONE_HOLDS = 'holds'
export const TONE_REFUTES = 'refutes'
export const TONE_UNREAD = 'unread'

const ELEMENT_NAMES = { Cl: 'chlorine', Br: 'bromine' }

const rFormat = new Intl.NumberFormat('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const ppmFormat = new Intl.NumberFormat('en', { maximumFractionDigits: 1 })
const factorFormat = new Intl.NumberFormat('en', { maximumFractionDigits: 1 })
const percentFormat = new Intl.NumberFormat('en', { style: 'percent', maximumFractionDigits: 0 })

const samples = (n) => `${n} sample${n === 1 ? '' : 's'}`
const spectra = (n) => `${n} spectr${n === 1 ? 'um' : 'a'}`
const atoms = (n, element) => `${n} ${ELEMENT_NAMES[element] ?? element}`

/** A halogen lock's tag. */
function lockTag(lock) {
  const element = lock.element
  if (lock.verdict === LOCK_LOCKED) return { label: `${element} lock`, tone: TONE_HOLDS }
  if (lock.verdict === LOCK_SILICON) return { label: `Si, not ${element}`, tone: TONE_REFUTES }
  if (lock.verdict === LOCK_NOT_LOCKED) return { label: `no ${element} lock`, tone: TONE_REFUTES }
  return { label: `${element} lock?`, tone: TONE_UNREAD }
}

/** A label twin's tag. */
function twinTag(twin) {
  if (twin.verdict === TWIN_PRESENT) return { label: '14N twin', tone: TONE_HOLDS }
  if (twin.verdict === TWIN_ABSENT) return { label: 'no 14N twin', tone: TONE_REFUTES }
  return { label: '14N twin?', tone: TONE_UNREAD }
}

/** A neutral pair's tag. */
function pairTag(pair) {
  if (pair.verdict === PAIR_ESTABLISHED) return { label: 'urea pair', tone: TONE_HOLDS }
  if (pair.verdict === PAIR_NOT_ESTABLISHED) return { label: 'pair apart', tone: TONE_REFUTES }
  return { label: 'pair?', tone: TONE_UNREAD }
}

const FACTS = [
  ['halogen_lock', lockTag],
  ['label_twin', twinTag],
  ['neutral_pair', pairTag]
]

/**
 * The ledger cell's tags, one per fact the record carries, each with its key,
 * short label and tone. Empty for no record.
 */
export function checkTags(record) {
  if (!record) return []
  return FACTS.filter(([key]) => record[key]).map(([key, tag]) => ({
    key,
    verdict: record[key].verdict,
    ...tag(record[key])
  }))
}

/**
 * What the ledger's Facts column sorts on: how many facts hold, less how many
 * refute; null for no record, so those sort last in both directions.
 */
export function checksRank(record) {
  const tags = checkTags(record)
  if (!tags.length) return null
  return tags.reduce(
    (rank, tag) => rank + (tag.tone === TONE_HOLDS ? 1 : tag.tone === TONE_REFUTES ? -1 : 0),
    0
  )
}

/** A halogen lock's account. */
function lockSentence(lock) {
  const element = lock.element
  const line = `its ${lock.label} line`
  const whose =
    lock.n_neutral > 0
      ? `${atoms(lock.n_neutral, element)} in the compound`
      : "the reagent's, not the compound's"
  switch (lock.verdict) {
    case LOCK_LOCKED:
      return (
        `Halogen lock: ${line} holds the share of ${atoms(lock.n_ion, element)} at the exact ` +
        `spacing, in ${lock.holds} of the ${samples(lock.judged)} that could tell (${whose}).`
      )
    case LOCK_SILICON:
      return (
        `Not a ${ELEMENT_NAMES[element]} lock: ${line} sits where a 30Si line would, and the ` +
        "29Si line a silicon reading implies holds across the batch, so the line is silicon's."
      )
    case LOCK_NOT_LOCKED:
      if (lock.reason === 'not_found') return `No halogen lock: no batch peak stands at ${line}.`
      if (lock.reason === 'another_parent')
        return `No halogen lock: ${line} is another compound's isotope line.`
      if (lock.reason === 'offset')
        return (
          `No halogen lock: ${line} sits ${ppmFormat.format(Math.abs(lock.offset_error_ppm))} ppm ` +
          `off the exact spacing, outside the batch's ${ppmFormat.format(lock.window_ppm)} ppm.`
        )
      return (
        `No halogen lock: ${line} does not hold the share of ${atoms(lock.n_ion, element)} ` +
        `across the batch (${lock.holds} of ${samples(lock.judged)}).`
      )
    default:
      if (lock.reason === 'both_halogens')
        return 'No halogen lock read: the ion carries chlorine and bromine, whose lines share one M+2 peak.'
      if (lock.reason === 'unresolved')
        return `No halogen lock read: ${line} is not separated from another line at this resolution.`
      if (lock.reason === 'no_offset')
        return (
          `No halogen lock read: no sample ${line} was stamped in for this compound still ` +
          'holds both lines, so its spacing was not read.'
        )
      if (lock.reason === 'silicon_untestable')
        return `No halogen lock read: ${line} could be a 30Si line, and too few samples show the 29Si line to tell.`
      return `No halogen lock read: too few samples can tell ${line}.`
  }
}

/** A label twin's account. */
function twinSentence(twin) {
  const excess =
    twin.excess != null && twin.excess >= 2
      ? `, the 14N line running ${factorFormat.format(twin.excess)} times the impurity's share`
      : ''
  // Where the reagent's own ions measured the impurity, the share is theirs.
  const measured =
    twin.impurity_factor != null
      ? ` The share is the one the reagent's own ions show, ${percentFormat.format(
          twin.impurity_factor
        )} of what the setup's purity gives.`
      : ''
  if (twin.verdict === TWIN_PRESENT)
    return (
      "14N twin: the label's impurity line one unit down stands at its share in " +
      `${twin.present} of the ${samples(twin.judged)} that would show it${excess}.${measured}`
    )
  if (twin.verdict === TWIN_ABSENT)
    return (
      "No 14N twin: the label's impurity line one unit down is missing from " +
      `${twin.judged - twin.present} of the ${samples(twin.judged)} that would show it, ` +
      "so this ion is not the label's." +
      measured
    )
  return (
    "14N twin not read: too few samples would show the label's impurity line " + `(${twin.judged}).`
  )
}

/** A neutral pair's account. */
function pairSentence(pair) {
  const partner = pair.partner_channel ?? 'its other channel'
  const reading =
    pair.r != null ? `: r ${rFormat.format(pair.r)} over ${spectra(pair.n_spectra ?? 0)}` : ''
  if (pair.verdict === PAIR_ESTABLISHED)
    return `Uronium pair: the compound's ${partner} peak co-varies with this one across the batch${reading}.`
  if (pair.verdict === PAIR_NOT_ESTABLISHED)
    return `Uronium pair apart: the compound's ${partner} peak does not co-vary with this one${reading}.`
  if (pair.reason === 'flat')
    return `Uronium pair not read: one of the two peaks hardly moves across the batch.`
  return `Uronium pair not read: the ${partner} peak and this one share too few spectra.`
}

/**
 * The full account: one sentence per fact, then that no tier reads them.
 * Empty for no record.
 */
export function checksTooltip(record) {
  if (!record) return ''
  const lines = []
  if (record.halogen_lock) lines.push(lockSentence(record.halogen_lock))
  if (record.label_twin) lines.push(twinSentence(record.label_twin))
  if (record.neutral_pair) lines.push(pairSentence(record.neutral_pair))
  if (!lines.length) return ''
  return [...lines, 'Shown for reading only: no tier moves on it.'].join(' ')
}

/** The inspector's one line: the facts' tags, in words. */
export function checksSummary(record) {
  const tags = checkTags(record)
  if (!tags.length) return ''
  return `Batch facts: ${tags.map((tag) => tag.label).join(', ')}`
}
