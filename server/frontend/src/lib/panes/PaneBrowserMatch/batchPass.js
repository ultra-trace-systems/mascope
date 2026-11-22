import { FOLD_STAGE } from '@/stores/data/modules/batchPeak/run.js'

import { PHASES, stageLabel, stagePhase } from './stores/batchAssign.js'

/**
 * What Assign batch's Details says of a pass
 * (docs/dev/batch_assignment_one_action.md, 3.2): its stages as a checklist
 * under the four phases, a one-line result for each that finished, the change
 * it made to the tier counts, and the settings it ran under. Pure, so the
 * popover (BatchAssignDetails.vue) only renders what these answer.
 */

/** The tiers the chips count, in confidence order, with their words. */
export const TIER_WORDS = Object.freeze({
  assigned: 'assigned',
  candidate: 'candidate',
  below_assignability: 'below assignability'
})

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`
const isCount = (value) => typeof value === 'number' && Number.isFinite(value)

/** The stages in the order a pass runs them, the fold first, from the status read. */
export function stageOrder(status) {
  return [
    {
      stage: FOLD_STAGE,
      label: stageLabel(status, FOLD_STAGE),
      phase: stagePhase(status, FOLD_STAGE)
    },
    ...(status?.stages ?? []).map(({ stage, label, phase }) => ({ stage, label, phase }))
  ]
}

/** Why a stage is stale, in words (MAS-983's marks). */
export function staleWords(reason, status) {
  if (!reason) return 'Out of date'
  if (reason.startsWith('upstream:')) {
    const stage = reason.slice('upstream:'.length)
    const label = status?.stages?.find((entry) => entry.stage === stage)?.label ?? stage
    return `${label} ran again since`
  }
  switch (reason) {
    case 'fold':
      return 'Samples were folded in since'
    case 'rebuild':
      return 'The ledger was rebuilt since'
    case 'import':
      return "An engine's result was imported since"
    case 'curation':
      return 'A batch peak was pinned or released since'
    case 'consensus':
      return 'The consensus was read again since'
    case 'partial':
      return 'It could not read every sample'
    case 'interrupted':
      return 'It was interrupted'
    default:
      return 'Out of date'
  }
}

/** Why a pass folded every sample first, in words (batch_stages.fold_reasons). */
const FOLD_REASONS = {
  no_batch_peaks: 'the batch had no batch peaks',
  import: "an engine's import was the current run",
  library_changed: 'a target collection or reference list changed',
  engine_changed: 'the last pass ran under another engine',
  asked: 'asked for: Re-assign from scratch'
}

/** The reasons a pass folded first, as one line; null for none. */
export function foldWords(reasons) {
  const words = (reasons ?? []).map((reason) => FOLD_REASONS[reason] ?? reason)
  if (!words.length) return null
  const line = words.join('; ')
  return `Folded because ${line}`
}

// One line per stage, in the words its help card and section of the user page
// use, from the counts its step's outcome keeps (batch_stages.PassRecord).
// Each answers null where a count it names is missing, and the outcome's own
// message stands in.
const RESULTS = {
  [FOLD_STAGE]: (d) =>
    isCount(d.samples_folded)
      ? `${plural(d.samples_folded, 'sample')} folded` +
        (d.samples_failed ? `, ${d.samples_failed} skipped` : '')
      : null,
  water_ladders: (d) =>
    isCount(d.rungs_passed) && isCount(d.rungs_tested) && isCount(d.claimed)
      ? `${d.rungs_passed} of ${plural(d.rungs_tested, 'rung')} passed, ` +
        `${plural(d.claimed, 'batch peak')} claimed`
      : null,
  known_species: (d) =>
    isCount(d.present) && isCount(d.species)
      ? `${d.present} of ${plural(d.species, 'known species', 'known species')} present`
      : null,
  search: (d) =>
    isCount(d.anchors_annotated) && isCount(d.anchors_searched)
      ? `${d.anchors_annotated} of ${plural(d.anchors_searched, 'batch peak')} ` +
        'assigned a composition'
      : null,
  isotope_lines: (strip, all) => {
    // The strip, then the stamp: one stage, two steps.
    const stamp = all[1] ?? {}
    const parts = []
    if (isCount(strip.stripped)) {
      parts.push(`${plural(strip.stripped, 'row')} folded under their compound`)
    }
    if (isCount(stamp.lines_stamped)) {
      parts.push(`${plural(stamp.lines_stamped, 'isotope line')} stamped`)
    }
    return parts.length ? parts.join(', ') : null
  },
  series: (d) =>
    isCount(d.families_committed) && isCount(d.anchors_in_series)
      ? `${plural(d.families_committed, 'series family', 'series families')}, ` +
        `${plural(d.anchors_in_series, 'batch peak')} in a series`
      : null,
  co_variation: (d) =>
    isCount(d.co_varying) && isCount(d.not_co_varying)
      ? `${plural(d.co_varying, 'pair')} of channels co-vary, ${d.not_co_varying} do not`
      : null,
  continuity: (d) =>
    isCount(d.pairs_graded)
      ? `${plural(d.pairs_graded, 'pair')} graded` +
        (d.grades && isCount(d.grades.red) ? `, ${d.grades.red} red` : '')
      : null,
  batch_checks: (d) =>
    isCount(d.locked) && isCount(d.twin_present) && isCount(d.labelled_ions)
      ? `${plural(d.locked, 'halogen lock')}, ${d.twin_present} of ` +
        `${plural(d.labelled_ions, 'labelled ion')} with their 14N twin`
      : null,
  blends: (d) => (isCount(d.anchors) ? `${plural(d.anchors, 'batch peak')} read again` : null)
}

/** The head of an outcome's message: what it says before the detail. */
function messageHead(message) {
  if (typeof message !== 'string' || !message.trim()) return null
  const head = message.split(/[:.](\s|$)/)[0].trim()
  return head || null
}

/**
 * A finished stage's result in one line, from its entry in the pass's
 * summary; null where it holds nothing to say.
 */
export function stageResult(entry) {
  const outcomes = Array.isArray(entry?.outcomes) ? entry.outcomes : []
  if (!outcomes.length) return null
  const data = outcomes.map((outcome) =>
    outcome?.data && typeof outcome.data === 'object' ? outcome.data : {}
  )
  const words = RESULTS[entry.stage]?.(data[0], data)
  return words ?? messageHead(outcomes[0]?.message)
}

/**
 * The plan of a pass still running: the stages it runs, the tail of the
 * order with the fold first where it folds. A pass runs from the stage it
 * starts at to the end, so `n_stages` alone says which.
 */
function plannedTail(order, nStages) {
  if (!Number.isInteger(nStages) || nStages <= 0) return null
  return order.slice(Math.max(order.length - nStages, 0)).map((entry) => entry.stage)
}

/**
 * The checklist of a pass: each stage of the order under its phase, with the
 * state the pass left it in.
 *
 * States: `done`, `running` (with `percent` where its packets give one),
 * `failed` (with `error`), `cancelled`, `stale` (with `reason`, from the
 * status read's `stale_stages`), `waiting` (a stage the running pass has yet
 * to reach) and `not_run` (one the pass does not run: before the stage it
 * started at, or a fold it did not need).
 *
 * @param {object} options
 * @param {object|null} options.status The batch's status read.
 * @param {object|null} options.run The pass's run, as the runs route serves it.
 * @param {object|null} options.live The running pass's newest packet: `stage`,
 *   `stage_index`, `n_stages`; ahead of the status read.
 * @param {number|null} options.percent The running stage's own progress, 0 to 100.
 * @returns {{phase: string, stages: object[]}[]}
 */
export function passChecklist({ status, run, live = null, percent = null }) {
  const order = stageOrder(status)
  const standing = new Map((status?.stages ?? []).map((entry) => [entry.stage, entry]))
  // The pass Details tells of is the batch's newest, so the stale marks of
  // now are on its own stages.
  const stale = new Set(status?.stale_stages ?? [])
  const summary = run?.summary && typeof run.summary === 'object' ? run.summary : {}
  const entries = Array.isArray(summary.stages)
    ? new Map(summary.stages.map((entry) => [entry.stage, entry]))
    : null
  const running = run?.status === 'running'

  let planned = null
  let at = null
  if (running) {
    const progress = { ...(status?.running ?? {}), ...(summary.progress ?? {}), ...(live ?? {}) }
    planned = plannedTail(order, progress.n_stages)
    at = progress.stage ?? null
  }

  const rows = order.map(({ stage, label, phase }) => {
    const row = { stage, label, phase, state: 'not_run', result: null }
    if (running) {
      if (planned && !planned.includes(stage)) return row
      if (stage === at) return { ...row, state: 'running', percent }
      const done = at != null && planned ? planned.indexOf(stage) < planned.indexOf(at) : false
      return { ...row, state: done ? 'done' : 'waiting' }
    }
    const entry = entries?.get(stage)
    if (entry) {
      row.state = entry.status === 'done' ? 'done' : (entry.status ?? 'not_run')
      if (entry.status === 'done') row.result = stageResult(entry)
      // A failed stage says why; so does one an interruption cancelled
      // (assign_batch stops it with its error), unlike a person's cancel.
      if (entry.error) row.error = entry.error
      // The fold runs only when the ledger cannot be the pass's starting
      // point (the note's 4.2): one it skipped needed none, and one it ran
      // says why.
      if (stage === FOLD_STAGE) {
        if (row.state === 'not_run') row.skipped = true
        else row.reason = foldWords(entry.reasons ?? summary.fold?.reasons)
      }
    }
    if (stale.has(stage) && (row.state === 'done' || row.state === 'not_run')) {
      row.state = 'stale'
      row.reason = staleWords(standing.get(stage)?.stale_reason, status)
    }
    return row
  })

  return PHASES.map((phase) => ({
    phase,
    stages: rows.filter((row) => row.phase === phase)
  })).filter((group) => group.stages.length)
}

/**
 * The batch peaks of each tier in a ledger, isotope lines aside: the
 * population the status line's "+N assigned" counts.
 */
export function tierCounts(rows) {
  const counts = Object.fromEntries(Object.keys(TIER_WORDS).map((tier) => [tier, 0]))
  for (const row of rows ?? []) {
    if (row?.line_owner_id) continue
    if (row?.consensus_tier in counts) counts[row.consensus_tier] += 1
  }
  return counts
}

/**
 * The change between two tier counts, as chips: one per tier that moved,
 * in confidence order, signed.
 */
export function tierChanges(before, after) {
  if (!before || !after) return []
  return Object.keys(TIER_WORDS)
    .map((tier) => ({ tier, delta: (after[tier] ?? 0) - (before[tier] ?? 0) }))
    .filter(({ delta }) => delta !== 0)
    .map(({ tier, delta }) => ({
      tier,
      delta,
      label: `${delta > 0 ? '+' : '−'}${Math.abs(delta)} ${TIER_WORDS[tier]}`
    }))
}

/**
 * The run a pass started from: the batch's newest completed run older than
 * it. That run was current as the pass started, and its snapshot is the
 * ledger as the pass found it.
 *
 * @param {object[]} runs The batch's runs, newest first.
 * @param {object} pass The pass.
 */
export function runBefore(runs, pass) {
  const list = runs ?? []
  const index = list.findIndex((run) => run.batch_peak_run_id === pass?.batch_peak_run_id)
  if (index === -1) return null
  return list.slice(index + 1).find((run) => run.status === 'completed') ?? null
}

const auto = (value) => (value == null || value === 'auto' ? 'Auto' : String(value))

/**
 * The settings a pass ran under, as lines of label and value, from what its
 * run recorded it was asked for (`config.settings`, batch_stages.pass_config);
 * empty for a run that recorded none.
 */
export function settingsLines(config) {
  const s = config?.settings
  if (!s || typeof s !== 'object') return []
  const lines = [
    ['Chemistry profile', auto(s.profile)],
    ['Context', auto(s.context)],
    ['m/z precision', s.mz_precision_ppm == null ? 'Auto' : `${s.mz_precision_ppm} ppm`],
    ['Formula range', s.formula_ranges || 'Default'],
    ['Peak ceiling', s.max_untargeted_peaks == null ? 'None' : String(s.max_untargeted_peaks)],
    ['Intensity threshold', s.peak_intensity_threshold],
    ['Alternatives kept', s.max_alternatives],
    [
      'Tier bands',
      s.assigned_threshold != null && s.candidate_threshold != null
        ? `assigned ≥ ${s.assigned_threshold}, candidate ≥ ${s.candidate_threshold}`
        : null
    ]
  ]
  return lines
    .filter(([, value]) => value != null && value !== '')
    .map(([label, value]) => ({ label, value: String(value) }))
}

/** Where a pass's settings came from, in words; null where it does not say. */
export function settingsSource(config) {
  if (config?.settings_source === 'batch') return "The batch's settings"
  if (config?.settings_source === 'request') return "Given to this run, in place of the batch's"
  return null
}
