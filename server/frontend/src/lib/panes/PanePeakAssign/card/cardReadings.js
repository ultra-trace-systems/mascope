/**
 * What the inspector card reads off a row, its run and its detail, as plain
 * functions: the bands its evidence is drawn against, the order and tone of
 * its reasons, the batch's groups a reason spans, and the four measurement
 * tiles, their titles and the reasons an empty one gives. The card's sections
 * show them (docs/dev/assignment_ledger_and_card.md, section 4.1); here they
 * can be tested without mounting anything.
 */

import { num } from '@/lib/formatters'
import { holdsTierDown, reasonFigure, reasonIcon } from '@/lib/tierReasons'
import { TIER_META } from '@/lib/tiers'
import { LINE_UNTESTABLE } from '@/lib/predictedEnvelope'

/**
 * The bands the engine tiers with when a run records none: evidence at or
 * over 75 % is assigned, at or over 45 % a candidate
 * (`PeakAssignmentConfig.tier_bands`).
 */
export const DEFAULT_TIER_BANDS = Object.freeze({ candidate: 0.45, assigned: 0.75 })

const isFraction = (value) => typeof value === 'number' && Number.isFinite(value)

/**
 * The run's evidence bands, as fractions, with whether they are the run's own
 * or the engine's defaults.
 *
 * @param {object|null} bands - the run's `tier_bands`
 * @returns {{candidate: number, assigned: number, own: boolean}}
 */
export function bandsOf(bands) {
  if (bands && isFraction(bands.candidate) && isFraction(bands.assigned)) {
    return { candidate: bands.candidate, assigned: bands.assigned, own: true }
  }
  return { ...DEFAULT_TIER_BANDS, own: false }
}

/**
 * Where on the bar, in percent of its width, a fraction of evidence sits:
 * clamped to the bar.
 *
 * @param {number} value
 * @returns {number}
 */
export const barPosition = (value) => Math.max(0, Math.min(100, value * 100))

/**
 * The edge of the band a row's evidence falls short of: the assigned band's
 * for evidence between the two, the candidate band's below both, none at or
 * above the assigned band.
 *
 * @param {number|null} evidence
 * @param {{candidate: number, assigned: number}} bands - `bandsOf`'s bands
 * @returns {number|null}
 */
export function bandMissed(evidence, bands) {
  if (!isFraction(evidence) || evidence >= bands.assigned) return null
  return evidence >= bands.candidate ? bands.assigned : bands.candidate
}

/**
 * A share as a percentage to `digits` decimals ("74.6%"), rounded as the
 * server's sentences round it; '-' where there is no number.
 *
 * @param {number|null} value
 * @param {number} [digits]
 * @returns {string}
 */
export function formatShare(value, digits = 0) {
  return isFraction(value) ? `${(value * 100).toFixed(digits)}%` : '-'
}

/**
 * How many decimals a share needs not to read as the band edge it misses,
 * the server's rule (`tiering._digits`): evidence of 0.746 under a band of
 * 0.75 reads "74.6%", never "75%", which would put it on the band.
 *
 * @param {number|null} value
 * @param {number|null} against - the band edge, or null where it misses none
 * @returns {number}
 */
export function shareDigits(value, against) {
  if (!isFraction(value) || !isFraction(against)) return 0
  for (const digits of [0, 1]) {
    if (formatShare(value, digits) !== formatShare(against, digits)) return digits
  }
  return 2
}

/**
 * The Why box's title: "Why assigned", "Why candidate", "Why below",
 * "Why unassigned" - the tier's short label, as its chip says it.
 *
 * @param {string|null} tier
 * @returns {string}
 */
export function whyTitle(tier) {
  const label = TIER_META[tier]?.label ?? 'unassigned'
  return `Why ${label}`
}

// A figure that is a measure, a share, widths or a count ("21%", "4.2 widths",
// "3 formulas"), not a formula: the formula a sentence names is usually the
// row's own, which the card's title already says.
const MEASURE = /%|\bwidths?\b|^\d+\s/

// A reason's name with what it turned on, for the line that says what holds a
// row: the isotope line an absent-line reason names ("15N"), else the measure
// its sentence turns on ("3 formulas").
function holdName(reason) {
  const figure = reasonFigure(reason)
  const what = reason.line ?? (MEASURE.test(figure) ? figure : '')
  return what ? `${reason.label} (${what})` : reason.label
}

// The reason a batch's decision on a listed compound writes on a row it put at
// candidate (known_species.py): the row stands there on the decision, not
// held from above.
const BATCH_DECISION = 'batch_known_species'

// What holds a row judged on its own reasons: the evidence band where its
// evidence is under the assigned one, then the rules that cap it, each named.
function holdParts(reasons, evidence, bands) {
  const caps = reasons.filter((reason) => reason.caps && reason.rule !== 'inherited_from_owner')
  const bandReason = reasons.find((reason) => reason.rule === 'evidence_band')
  const underAssigned = isFraction(evidence)
    ? evidence < bands.assigned && evidence >= bands.candidate
    : Boolean(bandReason)
  const band = Boolean(bandReason) && underAssigned
  const named = caps.map(holdName).join(', ')
  const parts = []
  if (band) {
    // As finely as it takes not to read as the band, as the card's product
    // line and the band's own sentence write it (tiering._digits).
    const digits = shareDigits(evidence, bands.assigned)
    parts.push(
      isFraction(evidence)
        ? `evidence ${formatShare(evidence, digits)} is under the assigned band of ${formatShare(bands.assigned, digits)}`
        : bandReason.label
    )
  }
  if (named) parts.push(band ? `also ${named}` : named)
  return { caps, band, parts }
}

const NO_REASON = 'no reason recorded - the run names no rule that holds'

/**
 * What holds a candidate row at candidate, in plain words, for the line the
 * Why box leads with and the tier chip's hover: a row whose fit is high reads
 * as a mistake at candidate unless the reason stands beside it.
 *
 * - The evidence band, where the evidence, fit times plausibility, is under
 *   the assigned band: a high fit at a low plausibility is held there.
 * - The rules that cap the row, each named with what it turned on.
 * - An isotopologue no rule of its own caps follows its M0, and reads what
 *   holds the M0: its band, with the M0's evidence, and its rules. The M0's
 *   band is named whether or not the run marked the inherited reason as
 *   capping, which it does only for a rule (tiering.py).
 * - A row the batch's decision put at candidate says that instead.
 * - A row the tiering pass judged and that names nothing that holds it says
 *   "no reason recorded": that is the engine's to answer, not the card's.
 *
 * A row no tiering pass judged (no reasons at all: an imported run, a hand
 * assignment, a row served from the batch ledger) gets no line, as the Why
 * box shows nothing for it.
 *
 * @param {object} options
 * @param {string|null} options.tier - the row's tier
 * @param {Array<object>} options.reasons - the row's `tierReasonsOf`
 * @param {Array<object>} [options.ownerReasons] - its M0's, for an isotopologue
 * @param {number|null} [options.evidence] - the row's evidence
 * @param {number|null} [options.ownerEvidence] - its M0's, for an isotopologue
 * @param {{candidate: number, assigned: number}} options.bands - `bandsOf`'s
 * @returns {{text: string, caps: Array<object>, viaM0: boolean, m0Capped: boolean,
 *   band: boolean}|null}
 */
export function candidateHold({
  tier,
  reasons,
  ownerReasons = [],
  evidence = null,
  ownerEvidence = null,
  bands
}) {
  if (tier !== 'candidate' || !Array.isArray(reasons) || !reasons.length) return null
  if (reasons.some((reason) => reason.rule === BATCH_DECISION)) {
    return {
      text: "At candidate on the batch's decision: the batch found this listed compound present over its files",
      caps: [],
      viaM0: false,
      m0Capped: false,
      band: false
    }
  }
  const inherited = reasons.find((reason) => reason.rule === 'inherited_from_owner')
  const own = holdParts(reasons, evidence, bands)
  const viaM0 = !own.caps.length && Boolean(inherited)
  if (!viaM0) {
    const text = own.parts.length
      ? `Held at candidate: ${own.parts.join('; ')}`
      : `Held at candidate: ${NO_REASON} this row`
    return { text, caps: own.caps, viaM0, m0Capped: false, band: own.band }
  }
  const owner = Array.isArray(ownerReasons) ? ownerReasons : []
  const m0 = holdParts(owner, ownerEvidence, bands)
  let text
  if (m0.parts.length) text = `Held at candidate with its M0: ${m0.parts.join('; ')}`
  else if (owner.length) text = `Held at candidate with its M0: ${NO_REASON} the M0`
  // Before the M0's detail arrives.
  else text = 'Held at candidate with its M0'
  return { text, caps: m0.caps, viaM0, m0Capped: inherited.caps === true, band: m0.band }
}

/**
 * The tier chip's cap mark on the card (BaseTierTag's `cap`), from
 * `candidateHold`: the rules that hold the row, named, or null where none
 * does - the evidence band is no rule, and the chip's hover names the
 * evidence already. An isotopologue whose M0 a rule capped is marked before
 * the M0's reasons arrive.
 *
 * @param {object|null} hold - `candidateHold`'s result
 * @returns {{label: string, tooltip: string}|null}
 */
export function holdCap(hold) {
  if (!hold || (!hold.caps.length && !hold.m0Capped)) return null
  const names = hold.caps.map((reason) => reason.label).join(', ')
  return {
    label: names
      ? `Held down by: ${names}${hold.viaM0 ? ', via its M0' : ''}`
      : 'Held down with its M0',
    tooltip: hold.text
  }
}

/** The tones a reason's line takes: what it does to the row's tier. */
export const REASON_HOLDS = 'holds-back'
export const REASON_SUPPORTS = 'supports'
export const REASON_NEUTRAL = 'neutral'

/**
 * What a reason does to the row: holds the tier back, supports it (what a row
 * kept its tier on), or neither (a reason that claims nothing, a grouping, an
 * isotopologue that follows its M0 without being taken down).
 *
 * @param {object} reason - one entry of `tierReasonsOf`
 * @returns {string} one of the `REASON_*` tones
 */
export function reasonTone(reason) {
  if (holdsTierDown(reason)) return REASON_HOLDS
  return reasonIcon(reason) === 'ph-check' ? REASON_SUPPORTS : REASON_NEUTRAL
}

/**
 * The reasons in the order the Why box lists them: those that hold the tier
 * back first, then the rest, each group in the order the run wrote it.
 *
 * @param {Array<object>} reasons - `tierReasonsOf`'s list
 * @returns {Array<object>}
 */
export function orderedReasons(reasons) {
  const list = Array.isArray(reasons) ? reasons : []
  return [...list.filter(holdsTierDown), ...list.filter((reason) => !holdsTierDown(reason))]
}

/**
 * The batch group a supporting reason spans: a second channel is the
 * neutral's other channels, a series anchor the series.
 */
const REASON_GROUP_KINDS = Object.freeze({
  corroborated: 'channels',
  series_anchor: 'series'
})

/**
 * The batch's group a reason spans, from the detail's `batch_groups`, or null
 * where the reason spans none or the batch serves no such group.
 *
 * A batch peak can sit in several series: its formula lattice can reach a
 * CH2 series and an O series. The series a `series_anchor` reason is about
 * is the row's own, the detail's `batch_series`, so its group is the one
 * with that unit and mechanism. The root is not matched: the batch serves
 * one group per lattice, rooted at its lightest root, so a record naming
 * another root of the same lattice still reads that group. Every served
 * group holds the peak, and the peak's own lattice fixes it among those of
 * one unit and mechanism. Without that record the group is read only when
 * the batch serves one series group, never guessed among several.
 *
 * @param {object} reason - one entry of `tierReasonsOf`
 * @param {Array<object>|null} groups - the detail's `batch_groups`
 * @param {object|null} [series] - the detail's `batch_series`
 * @returns {object|null}
 */
export function reasonGroup(reason, groups, series = null) {
  const kind = REASON_GROUP_KINDS[reason?.rule]
  if (!kind || !Array.isArray(groups)) return null
  const served = groups.filter((group) => group?.kind === kind && group.members?.length)
  if (kind !== 'series') return served[0] ?? null
  if (!series?.unit) return served.length === 1 ? served[0] : null
  return (
    served.find(
      (group) =>
        group.unit === series.unit &&
        (series.ionization_mechanism_id == null ||
          group.ionization_mechanism_id == null ||
          String(group.ionization_mechanism_id) === String(series.ionization_mechanism_id))
    ) ?? null
  )
}

// How a group's member is labelled beside its m/z: the ledger's lens labels
// it the same (lib/ledger/groups.js), so the card and the lens agree.
export { memberLabel } from '@/lib/ledger/groups.js'

/** Why the M0's abundance error reads `-` rather than the zero it carries. */
export const M0_ABUNDANCE_TOOLTIP =
  'The monoisotopic line (M0) is the line the other lines are measured against, so it has no abundance error of its own.'

/**
 * The four tiles' titles, in the words the user page and the rest of the card
 * use for each measurement (docs/user/how-it-works/peak-assignment.md, the
 * inspector card).
 */
export const TILE_TITLES = Object.freeze({
  mass: 'Mass error',
  isotopes: 'Isotope lines found',
  confidence: 'Confidence',
  batch: 'Batch'
})

/** What a dash stands for on a tile with nothing to show. */
export const EMPTY_REASONS = Object.freeze({
  mass: 'Not measured: the run recorded no m/z error for this row',
  confidence:
    'Not recorded: the run that wrote this row kept no confidence for it, so there is no ' +
    "share of the evidence among the peak's candidates to show",
  noBatchPeak:
    'No batch peak holds this peak: the batch pass has not run yet, or it gathered this ' +
    'peak into none',
  samples:
    "Not counted for this row: a batch peak's samples are counted on a row served from the " +
    "batch ledger, or where the batch's persistence stage took the peak"
})

/**
 * The isotope tile: the lines found of those predicted, from the family the
 * card lists and the lines the run asked for and did not find. Its tooltip
 * says what the count counts.
 *
 * @param {object} options
 * @param {Array<object>} options.family - the family's rows, the head included
 * @param {Array<object>} options.missing - `notFoundLines` of the head
 * @param {number|null} options.abundanceError - the row's abundance error
 * @param {boolean} [options.reference] - the row is the line the others are
 *   measured against (the M0), whose zero is no error of its own: its error
 *   reads `-`, and the caption's tooltip says why
 * @returns {{value: string, caption: string, captionTooltip: string|null, tooltip: string, found: number, predicted: number}}
 */
export function isotopeTile({ family, missing, abundanceError, reference = false }) {
  const found = Math.max(0, (family?.length ?? 0) - 1)
  const absent = (missing ?? []).filter((line) => line?.state !== LINE_UNTESTABLE).length
  const untested = (missing ?? []).length - absent
  const predicted = found + (missing ?? []).length
  const parts = []
  let captionTooltip = null
  if (abundanceError != null && reference) {
    parts.push('abundance error -')
    captionTooltip = M0_ABUNDANCE_TOOLTIP
  } else if (abundanceError != null) {
    parts.push(`abundance off by ${num.relativeAbundanceError.format(Math.abs(abundanceError))}`)
  }
  if (absent) parts.push(`${absent} not found`)
  if (untested) parts.push(`${untested} not tested`)
  if (!predicted) {
    return {
      value: 'M0 only',
      caption: 'no isotope line found or asked for',
      captionTooltip: null,
      tooltip: 'No isotope line was found or asked for: only the main peak, M0, is measured',
      found,
      predicted
    }
  }
  return {
    value: `${found} of ${predicted}`,
    caption: parts.length ? parts.join(' · ') : 'isotope lines found of those predicted',
    captionTooltip,
    tooltip: `${found} of ${predicted} predicted isotope line${predicted === 1 ? '' : 's'} found`,
    found,
    predicted
  }
}

/**
 * The batch tile: two labelled lines, the samples the peak's batch peak is in
 * and how many of the batch's readings the card lists for it; or one dash,
 * with its reason, where no batch peak holds the peak.
 *
 * @param {object} options
 * @param {number|null} options.present - samples the batch peak is in
 * @param {number|null} options.total - samples in the batch, where known
 * @param {number} options.readings - the batch readings the card lists
 * @param {boolean} options.inBatch - whether the peak sits in a batch peak
 * @returns {{lines: Array<{key: string, label: string, value: string, tooltip: string}>}
 *   | {lines: null, value: string, caption: string, tooltip: string}}
 */
export function batchTile({ present, total, readings, inBatch }) {
  const counted = Number.isInteger(present)
  if (!counted && !inBatch && !readings) {
    return {
      lines: null,
      value: '—',
      caption: 'in no batch peak',
      tooltip: EMPTY_REASONS.noBatchPeak
    }
  }
  const of = counted && Number.isInteger(total) ? ` of ${total}` : ''
  return {
    lines: [
      {
        key: 'samples',
        label: 'Samples it is in',
        value: counted ? `${present}${of}` : '—',
        tooltip: counted
          ? `The batch peak is in ${present}${of} sample${present === 1 && !of ? '' : 's'}`
          : EMPTY_REASONS.samples
      },
      {
        key: 'readings',
        label: 'Batch readings',
        value: String(readings),
        tooltip: `${readings} reading${readings === 1 ? '' : 's'} of the batch stages, listed under Across the batch`
      }
    ]
  }
}

/**
 * A run as the card names it: its place among the sample's runs, as the run
 * selector numbers them (oldest #1), and when it finished.
 *
 * @param {object|null} run - the run record
 * @param {Array<object>} runs - the sample's runs, newest first
 * @returns {string} "run #2 of 3, completed 20/11/2026, 14:05:00", or '' for no run
 */
export function runName(run, runs) {
  if (!run) return ''
  const list = runs ?? []
  const index = list.findIndex((r) => r.peak_assignment_run_id === run.peak_assignment_run_id)
  const ordinal = index === -1 ? 'run' : `run #${list.length - index} of ${list.length}`
  const finished = run.peak_assignment_run_utc_completed ?? run.peak_assignment_run_utc_created
  const date = finished ? new Date(finished) : null
  return date && !Number.isNaN(date.getTime())
    ? `${ordinal}, ${run.peak_assignment_run_utc_completed ? 'completed' : 'started'} ${date.toLocaleString()}`
    : ordinal
}

// How the notice says why a run newer than the sample's result is not it yet.
const NEWER_RUN_STATE = {
  pending: 'waiting to start',
  running: 'still running',
  failed: 'that failed',
  cancelled: 'that was cancelled'
}

/**
 * The notice the card shows over a run that is not the sample's result, and
 * why its controls are off. A decoy arm comes first: a decoy is never the
 * sample's result, whatever else is true of it. Then any run that is not the
 * sample's latest completed one, named for what it is:
 *
 * - the batch ledger (engine 'batch'), the ledger derived from the batch
 *   peaks rather than a run of the sample. The runs listing puts it last, and
 *   its completion time is the last write to a batch peak the sample sits in,
 *   so neither its place nor its date says it is older or newer;
 * - a run listed before the latest completed one, which is newer and has not
 *   completed: waiting, running, failed or cancelled;
 * - any other run, an earlier one.
 *
 * The sample's own result, and a sample with no completed run to compare
 * against, get none.
 *
 * @param {object|null} run - the run the card reads
 * @param {object|null} latest - the sample's latest completed run
 * @param {Array<object>} runs - the sample's runs, newest first
 * @returns {{kind: 'decoy'|'ledger'|'newer'|'earlier', text: string, reason: string}|null}
 */
export function runNotice(run, latest, runs) {
  if (!run) return null
  if (run.decoy) {
    return {
      kind: 'decoy',
      text: `A decoy run, arm ${run.decoy}: the engine re-run with its chemistry made wrong on purpose`,
      reason:
        "A decoy run is never the sample's result: verdicts and changes are not recorded on it"
    }
  }
  if (!latest || run.peak_assignment_run_id === latest.peak_assignment_run_id) return null
  const select = 'Select it in the run selector to record one.'
  if (run.engine === 'batch') {
    const written = run.peak_assignment_run_utc_completed
      ? new Date(run.peak_assignment_run_utc_completed)
      : null
    const when =
      written && !Number.isNaN(written.getTime())
        ? `, last written ${written.toLocaleString()}`
        : ''
    return {
      kind: 'ledger',
      text: `Read only: the batch ledger, derived from the batch peaks${when}`,
      reason:
        'The batch ledger is not a run of this sample. Verdicts and changes are recorded on ' +
        `the sample's latest completed run. ${select}`
    }
  }
  const list = runs ?? []
  const index = list.findIndex((r) => r.peak_assignment_run_id === run.peak_assignment_run_id)
  const latestIndex = list.findIndex(
    (r) => r.peak_assignment_run_id === latest.peak_assignment_run_id
  )
  if (index !== -1 && latestIndex !== -1 && index < latestIndex) {
    const state = NEWER_RUN_STATE[run.status] ?? 'that has not completed'
    return {
      kind: 'newer',
      text: `Read only: a newer run ${state}, ${runName(run, runs)}`,
      reason:
        "This run has not completed, so it is not yet the sample's result. Verdicts and " +
        `changes are recorded on the sample's latest completed run. ${select}`
    }
  }
  return {
    kind: 'earlier',
    text: `Read only: an earlier run, ${runName(run, runs)}`,
    reason: `Verdicts and changes are recorded on the sample's latest completed run. ${select}`
  }
}
