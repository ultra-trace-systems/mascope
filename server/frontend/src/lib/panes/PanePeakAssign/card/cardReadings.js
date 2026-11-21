/**
 * What the inspector card reads off a row, its run and its detail, as plain
 * functions: the bands its evidence is drawn against, the order and tone of
 * its reasons, the batch's groups a reason spans, and the four measurement
 * tiles. The card's sections show them (docs/dev/assignment_ledger_and_card.md,
 * section 4.1); here they can be tested without mounting anything.
 */

import { num } from '@/lib/formatters'
import { holdsTierDown, reasonIcon } from '@/lib/tierReasons'
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
 * @param {object} reason - one entry of `tierReasonsOf`
 * @param {Array<object>|null} groups - the detail's `batch_groups`
 * @returns {object|null}
 */
export function reasonGroup(reason, groups) {
  const kind = REASON_GROUP_KINDS[reason?.rule]
  if (!kind || !Array.isArray(groups)) return null
  return groups.find((group) => group?.kind === kind && group.members?.length) ?? null
}

const signed = new Intl.NumberFormat('en-US', { signDisplay: 'always' })

/**
 * How a group's member is labelled beside its m/z: a series member by its
 * step from the root ("root", "+1 CH2", "-2 CH2"), any other by its channel,
 * else its formula.
 *
 * @param {object} member - one of a group's `members`
 * @param {object} group - the group
 * @param {object|null} [series] - the detail's `batch_series`, whose `unit`
 *   names a series step
 * @returns {string}
 */
export function memberLabel(member, group, series = null) {
  if (group?.kind === 'series' && Number.isInteger(member?.step)) {
    if (member.step === 0) return 'root'
    const unit = series?.unit ?? 'unit'
    return `${signed.format(member.step)} ${unit}`
  }
  return member?.mechanism || member?.ion_formula || member?.formula || 'another row'
}

/**
 * The isotope tile: the lines found of those predicted, from the family the
 * card lists and the lines the run asked for and did not find.
 *
 * @param {object} options
 * @param {Array<object>} options.family - the family's rows, the head included
 * @param {Array<object>} options.missing - `notFoundLines` of the head
 * @param {number|null} options.abundanceError - the row's abundance error
 * @returns {{value: string, caption: string, found: number, predicted: number}}
 */
export function isotopeTile({ family, missing, abundanceError }) {
  const found = Math.max(0, (family?.length ?? 0) - 1)
  const absent = (missing ?? []).filter((line) => line?.state !== LINE_UNTESTABLE).length
  const untested = (missing ?? []).length - absent
  const predicted = found + (missing ?? []).length
  const parts = []
  if (abundanceError != null) {
    parts.push(`abundance off by ${num.relativeAbundanceError.format(Math.abs(abundanceError))}`)
  }
  if (absent) parts.push(`${absent} not found`)
  if (untested) parts.push(`${untested} not tested`)
  if (!predicted) {
    return { value: 'M0 only', caption: 'no isotope line found or asked for', found, predicted }
  }
  return {
    value: `${found} of ${predicted}`,
    caption: parts.length ? parts.join(' · ') : 'isotope lines found of those predicted',
    found,
    predicted
  }
}

/**
 * The batch tile: the samples the peak's batch peak is in, and how many of the
 * batch's readings the card lists for it.
 *
 * @param {object} options
 * @param {number|null} options.present - samples the batch peak is in
 * @param {number|null} options.total - samples in the batch, where known
 * @param {number} options.readings - the batch readings the card lists
 * @param {boolean} options.inBatch - whether the peak sits in a batch peak
 * @returns {{value: string, caption: string}}
 */
export function batchTile({ present, total, readings, inBatch }) {
  const readingsWord = `${readings} batch reading${readings === 1 ? '' : 's'}`
  if (Number.isInteger(present)) {
    const of = Number.isInteger(total) ? ` of ${total}` : ''
    return {
      value: `${present}${of}`,
      caption: `sample${present === 1 && !of ? '' : 's'} it is in · ${readingsWord}`
    }
  }
  if (inBatch || readings) return { value: '—', caption: readingsWord }
  return { value: '—', caption: 'in no batch peak' }
}
