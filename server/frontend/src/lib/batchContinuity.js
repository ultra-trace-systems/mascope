/**
 * What the batch's ratio-stability pass says of a batch peak, in words.
 *
 * The pass (step 4.11 of the assignment plan, `continuity.py`) reads the
 * within-sample ratio of every pair of related batch peaks - an isotope line
 * and its main peak, two channels of one compound - across a time-ordered
 * batch, and grades it. A batch peak carries its most alarming pair's grade as
 * `continuity_grade`, the pairs under `continuity`, and `continuity_stale`
 * where a sample folded since, a curation or a re-fold moved it. It is
 * display-only: no tier reads it, and these words say so.
 */

export const GRADE_RED = 'red'
export const GRADE_DRIFT = 'drift'
export const GRADE_STABLE = 'stable'
export const GRADE_INCONCLUSIVE = 'inconclusive'
export const GRADE_INSUFFICIENT = 'insufficient'

/** The grades in the pass's order of alarm, which the column sorts on. */
export const GRADES = [GRADE_RED, GRADE_DRIFT, GRADE_STABLE, GRADE_INCONCLUSIVE, GRADE_INSUFFICIENT]

/** The grades that say something of the pair; the other two say it could not. */
export const CLAIM_GRADES = [GRADE_RED, GRADE_DRIFT, GRADE_STABLE]

/** The anchor's warning where the pass left some of its pairs ungraded. */
export const WARNING_PARTIAL = 'partial'

const PAIR_ISOTOPOLOGUE = 'isotopologue'

export const GRADE_META = {
  [GRADE_RED]: {
    label: 'Red',
    meaning:
      'the ratio drifts with nothing saying the two traces co-vary, or it jumps: two species ' +
      'may share a trace'
  },
  [GRADE_DRIFT]: {
    label: 'Drift',
    meaning:
      "the ratio drifts while the two channels' traces co-vary: the source's chemistry moved " +
      'and the assignment stands'
  },
  [GRADE_STABLE]: {
    label: 'Stable',
    meaning: 'the ratio holds flat across the batch, as one species keeps it'
  },
  [GRADE_INCONCLUSIVE]: {
    label: 'Inconclusive',
    meaning: 'the ratio is neither flat enough to be stable nor alarming'
  },
  [GRADE_INSUFFICIENT]: {
    label: 'Insufficient',
    meaning: 'too few samples or acquisition times hold both peaks to read the ratio'
  }
}

const DISPLAY_ONLY =
  'Shown for reading only: it says two peaks behave as one species, never which species, ' +
  'and no tier moves on it.'

const PARTIAL_SENTENCE =
  'Partial: the batch had more pairs than one pass grades, and some of these were left ungraded.'

const WITHDRAWN_REASONS = {
  gap:
    'The batch is not time-ordered: its largest gap between acquisition times is more than ' +
    'ten times the median gap, so no ratio is graded.',
  few_times: 'The batch has too few distinct acquisition times for any ratio to be graded.'
}

/** Whether the pass graded nothing on the anchor because the batch is not time-ordered. */
export function continuityWithdrawn(row) {
  return Boolean(row?.continuity && row.continuity.eligible === false && !row.continuity_grade)
}

/** Whether the pass left some of the anchor's pairs ungraded. */
export function continuityPartial(row) {
  return Boolean(row?.continuity?.warnings?.includes(WARNING_PARTIAL))
}

/** Whether the ledger has moved past the grade: a sample folded since, or the claim moved. */
export function continuityStale(row) {
  return Boolean(row?.continuity_stale)
}

/** The cell's text, or '' where the pass left nothing on the anchor. */
export function continuityLabel(row) {
  if (row?.continuity_grade) return GRADE_META[row.continuity_grade]?.label ?? row.continuity_grade
  return continuityWithdrawn(row) ? 'Not graded' : ''
}

/**
 * What the ledger's column sorts on: the grade's place in the order of alarm,
 * red first, null where there is no grade, so those sort last in both
 * directions.
 */
export function continuityRank(row) {
  const index = GRADES.indexOf(row?.continuity_grade)
  return index < 0 ? null : index
}

/** Why the grade is behind the ledger, as a sentence. */
function staleSentence(row) {
  const detail = row.continuity ?? {}
  if (detail.formula !== row.consensus_formula)
    return `Graded for ${detail.formula ?? 'no formula'} - the consensus is now ${
      row.consensus_formula ?? 'unassigned'
    }. It is graded again when the pass next runs.`
  if (
    detail.n_present_at_compute != null &&
    row.n_present != null &&
    detail.n_present_at_compute !== row.n_present
  )
    return `Graded at ${detail.n_present_at_compute} samples - the batch peak is in ${row.n_present} now. It is graded again when the pass next runs.`
  return 'The batch peak has moved since it was graded. It is graded again when the pass next runs.'
}

/**
 * What the reader must know before reading the grade, or '': that the batch is
 * not time-ordered, so nothing was graded, or that the ledger has moved past
 * the grade.
 */
export function continuityWarning(row) {
  if (!row?.continuity) return ''
  if (continuityWithdrawn(row))
    return WITHDRAWN_REASONS[row.continuity.reason] ?? 'The batch is not time-ordered.'
  return continuityStale(row) ? staleSentence(row) : ''
}

/** The full account in one tooltip: the grade's meaning, the warnings, and that it is shown only. */
export function continuityTooltip(row) {
  if (!row?.continuity) return ''
  const lines = []
  const meta = GRADE_META[row.continuity_grade]
  if (meta) {
    const pairs = row.continuity.n_pairs ?? row.continuity.pairs?.length ?? 0
    lines.push(
      `${meta.label} on its most alarming of ${pairs} pair${pairs === 1 ? '' : 's'}: ${meta.meaning}.`
    )
  }
  if (continuityPartial(row)) lines.push(PARTIAL_SENTENCE)
  const warning = continuityWarning(row)
  if (warning) lines.push(warning)
  lines.push(DISPLAY_ONLY)
  return lines.join(' ')
}

const fixed = (digits) =>
  new Intl.NumberFormat('en', { minimumFractionDigits: digits, maximumFractionDigits: digits })
const twoDigits = fixed(2)
const fourDigits = fixed(4)
const percent = new Intl.NumberFormat('en', { style: 'percent', maximumFractionDigits: 0 })
const ratioFormat = new Intl.NumberFormat('en', { maximumSignificantDigits: 3 })

/**
 * One pair as the popover lists it, from the anchor's own side: who the
 * partner is, the grade, the samples, the ratio, its spread and its drift,
 * and what the co-variation reading said of the two channels.
 *
 * @param {object} pair An entry of `continuity.pairs`.
 * @param {boolean} isotopeLine Whether the anchor is itself an isotope line,
 *   so that its isotope partner is the main peak rather than another line.
 */
export function pairLine(pair, isotopeLine = false) {
  const isotope = pair.kind === PAIR_ISOTOPOLOGUE
  const partner = isotope ? (isotopeLine ? 'Main peak' : 'Isotope line') : 'Other channel'
  const drift =
    pair.drift_log == null
      ? 'not tested'
      : `${pair.drift_log >= 0 ? 'rises' : 'falls'} ${twoDigits.format(pair.drift_fold)}x` +
        (pair.rho == null ? '' : `, rho ${twoDigits.format(pair.rho)}`)
  const coVariation = pair.co_variation
    ? pair.co_variation.verdict === 'co_varying'
      ? `co-varies, r ${twoDigits.format(pair.co_variation.r)}`
      : pair.co_variation.verdict === 'not_co_varying'
        ? `does not co-vary, r ${twoDigits.format(pair.co_variation.r)}`
        : 'co-variation untestable'
    : null
  return {
    key: `${pair.kind}:${pair.partner_batch_peak_id}`,
    partner,
    mz: pair.partner_mz == null ? '' : fourDigits.format(pair.partner_mz),
    grade: pair.grade,
    gradeLabel: GRADE_META[pair.grade]?.label ?? pair.grade,
    samples: pair.n ?? 0,
    ratio: pair.ratio_median == null ? '-' : ratioFormat.format(pair.ratio_median),
    spread: pair.ratio_spread == null ? '-' : percent.format(pair.ratio_spread),
    drift,
    coVariation
  }
}

/** The popover's footnote on how many pairs it lists, or '' when it lists all. */
export function pairsShownNote(row) {
  const detail = row?.continuity
  const listed = detail?.pairs?.length ?? 0
  const total = detail?.n_pairs ?? listed
  if (total <= listed) return ''
  return `Showing ${listed} of ${total} pairs: the most alarming first, then the best supported.`
}

export { DISPLAY_ONLY as CONTINUITY_DISPLAY_ONLY, PARTIAL_SENTENCE as CONTINUITY_PARTIAL }
