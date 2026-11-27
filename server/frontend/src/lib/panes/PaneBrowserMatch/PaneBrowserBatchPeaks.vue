<script setup>
import { reactive, ref, computed, nextTick, watch } from 'vue'

import { api } from '@/api'
import { getApiErrorMessage } from '@/api/utils'

import Button from 'primevue/button'
import Column from 'primevue/column'
import Message from 'primevue/message'
import Popover from 'primevue/popover'
import SelectButton from 'primevue/selectbutton'
import { FilterMatchMode } from '@primevue/core/api'

import {
  BaseCopyableField,
  BaseEmptyState,
  BaseEvidenceBadge,
  BaseLevelTag,
  BaseTierTag
} from '@/lib/base'
import { ledgerListing, listingName, targetName, targetOf } from '@/lib/referenceListings'
import { TONE_HOLDS_BACK, ledgerMarks, markOf } from '@/lib/evidence/marks'
import { isPredicted, stampParentId, stampTooltip } from '@/lib/batchIsotopologues'
import {
  strippedLinesCount,
  strippedParentId,
  strippedTooltip
} from '@/lib/batchMergedIsotopologues'
import { blendOf } from '@/lib/batchBlend'
import { flatSeriesOf } from '@/lib/batchFlatSeries'
import { holdingReasons, holdingTooltip } from '@/lib/batchPersistence'
import { bucketOf, bucketRank, engineBucketOf, tierName } from '@/lib/tiers'
import {
  isLevel,
  isShownLevel,
  levelFilterOptions,
  levelMeta,
  levelRank,
  levelTooltip,
  memberLevelTooltip,
  batchLevelOf,
  batchLevelTooltip,
  readerLevel
} from '@/lib/levels'
import { VERDICT_META } from '@/lib/verification'
import {
  AssignmentLedger,
  BADGE_FILTER_KINDS,
  BATCH_SEARCH_PPM,
  ENTRY_GAP,
  ENTRY_MEMBER,
  GROUP_CHANNELS,
  GROUP_CHECK_PAIR,
  GROUP_CO_VARIATION,
  GROUP_SERIES,
  LEDGER_ROW_HEIGHT,
  LedgerLevelSources,
  LedgerMenuChoice,
  LedgerVerdictPopover,
  LedgerViewToggle,
  batchGroupSets,
  batchLineLabel,
  batchLineName,
  groupFor,
  groupSections,
  groupsByMember,
  lensColumnLabel,
  lensEntries,
  lensTitle,
  ledgerTierChips,
  useFamilyFold,
  useLedgerFilters,
  useLedgerLensStore,
  useLedgerSort,
  useLedgerView,
  useRowPopover,
  verdictRank
} from '@/lib/ledger'
import { useApp } from '@/stores'
import { scrollVirtualRowIntoView } from '@/lib/virtualScroll'

import { useBatchPeakJump } from './stores/batchPeakJump.js'
import { INSTRUMENT_TYPE_LABELS } from '@/lib/batchChart'
import { MAX_SELECTED_BATCH_PEAKS } from '@/stores/data/modules/batchPeak/ledger'
import { runLabel } from '@/stores/data/modules/batchPeak/run.js'

import { useBatchAssign } from './stores/batchAssign.js'
import { useBatchChartSelection } from './stores/batchChartSelection.js'
import { useBatchLedgerReturn } from './stores/batchLedgerReturn.js'
import BatchAcrossPopover from './BatchAcrossPopover.vue'
import BatchPeakContinuityPopover from './BatchPeakContinuityPopover.vue'

/**
 * The batch scope of the assignment ledger (lib/ledger/AssignmentLedger.vue):
 * the selection surface for the peak-centric batch overview. A virtual-scrolled,
 * multi-select table of the batch's batch peaks (cross-sample m/z anchors); the
 * rows the user selects here are exactly what the Assignments chart plots, so
 * the chart never renders 1000+ traces at once. This pane is the scope's
 * configuration: its rows, its own columns (the sample count, and the batch's
 * readings as badges, Across the batch), its strip items (the instrument
 * partition, the history banner, the selection cap), its row action (open the
 * brightest sample) and the capped multiple selection.
 */
const app = useApp()
// The row action that opens the brightest sample on this peak.
const jump = useBatchPeakJump()

// Assign batch lives a row up, in the browser's switch bar
// (BatchAssignControl.vue), so the launch and its state are shared through a
// store. The refusal is read back here, to be reported above the table it is
// about rather than in a toast that has scrolled away, and the empty ledger
// offers the action itself.
const assign = useBatchAssign()

// Every choice of the filter bar and its Show menu, held outside the pane and
// persisted (lib/ledger/useLedgerView.js), so it survives a visit to a sample
// and back, and a reload.
const view = useLedgerView('batch')
const viewChoice = (key) =>
  computed({
    get: () => view[key],
    set: (value) => (view[key] = value)
  })

const ledger = computed(() => app.data.batchPeak)
const verdicts = computed(() => app.data.batchPeakVerification)
// The batch's runs: which one the ledger is reading, and whether that is the
// live one. An earlier run is history - shown from its snapshot, read-only.
const runs = computed(() => app.data.batchPeakRun)
// The run on screen, named as Run history names it: its number in the batch's
// history and its label, or the label alone for a run the history no longer
// lists.
const viewedRunLabel = computed(() => {
  const run = runs.value.viewing
  if (!run) return ''
  const label = runLabel(run)
  const ordinal = runs.value.ordinal?.(run)
  return ordinal != null ? `run #${ordinal}, ${label},` : label
})

// --- Batch-level verdicts ----------------------------------------------------
// One judgment per species at a batch peak, recorded from the Verdict column's
// popover. A row's badge is the live verdict on its present claim, or - stale -
// the latest live one on a claim the consensus has since left; the projection
// carries a rank for the column to sort on, confirmed first and unjudged last.
const verdictFor = (row) => verdicts.value.forAnchor(row)
const verdictStale = (row) => {
  const record = verdictFor(row)
  return Boolean(record && verdicts.value.isStale(record, row))
}
// The cell's own tooltip covers the two states the badge has none for: no
// verdict yet, and a stale one. A current badge speaks for itself.
const verdictTooltip = (row) => {
  const record = verdictFor(row)
  if (!record) return 'Record a batch-level verdict'
  if (!verdicts.value.isStale(record, row)) return ''
  const label = VERDICT_META[record.verdict]?.label ?? record.verdict
  return `${label} as ${record.assigned_formula} - the consensus is now ${
    row.consensus_formula ?? 'unassigned'
  }. Re-judge or retract.`
}
const verdictCell = (row) => ({
  record: verdictFor(row),
  stale: verdictStale(row),
  disabled: !runs.value.viewingCurrent,
  label: verdictFor(row) ? 'Batch-level verdict' : 'Record a batch-level verdict',
  tooltip: verdictTooltip(row)
})

// --- Isotopologue families --------------------------------------------------

// Batch peaks are bare m/z anchors, so the family link is one the server
// derives and states once per isotope line (`line_owner_id`, MAS-865): the
// members' own isotopologue link, else the parent a stamp names. The
// continuity pass pairs the row with it too, so a badge describes the pair the
// row is drawn in.
//
// It is deliberately ONE hop, observed rather than reconciled: a chain (an
// isotopologue of an isotopologue) has to reach a row that is actually drawn,
// and a link out of the list - to an anchor the server filtered out, or one
// deleted since - has to fail into a top-level row rather than into a row that
// is never rendered at all.
const byId = computed(() => new Map(ledger.value.list.map((bp) => [bp.batch_peak_id, bp])))
const familyLink = (row) => row.line_owner_id ?? null

/**
 * The row this one folds under, or null when it stands on its own.
 *
 * Walks up to the family's root so the table stays two levels deep, which is
 * what lets it keep a fixed row height and virtual-scroll. A cycle ends the
 * walk with no parent, so the row is drawn rather than lost.
 */
const rootParentId = (row, index) => {
  const seen = new Set([row.batch_peak_id])
  let parentId = familyLink(row)
  while (parentId && !seen.has(parentId)) {
    const parent = index.get(parentId)
    if (!parent) return null
    if (!familyLink(parent)) return parentId
    seen.add(parentId)
    parentId = familyLink(parent)
  }
  return null
}

// --- The instrument partition -------------------------------------------------

// A batch of both instrument types has a ledger of each, since an Orbitrap's
// heights and a TOF's areas cannot share a trace. The table, the tier strip
// and the chart show one at a time; the picker offers the other only when the
// batch has one. The store holds the choice, so the chart reads the same one.
const shown = computed(() => ledger.value.list.filter((bp) => ledger.value.inPartition(bp)))
const instrumentOptions = computed(() =>
  ledger.value.instrumentTypes.map((type) => ({ label: INSTRUMENT_TYPE_LABELS[type], value: type }))
)
const instrumentShown = computed({
  get: () => ledger.value.instrumentType,
  set: (type) => {
    if (type) ledger.value.showInstrument(type)
  }
})

// What the formula column shows: the consensus formula, or on an anchor a
// source role accounts for, the ion the peak is - written with its charge, as
// the sample ledger shows a reagent row's - since the role chip beside it says
// whose ion it is. A row with neither is blank.
const formulaOf = (row) =>
  row.consensus_formula || (row.consensus_role ? row.consensus_ion_formula : null) || null
const ION_TOOLTIP = 'Ion formula: the peak names an ion, and no compound of the sample'

// --- The importing engine's own tier and level ----------------------------------

// A batch import can carry the engine's own verdict on each identity it lands,
// kept beside the consensus tier this server measures rather than in its
// place. Offered only where some batch peak of the instrument type shown
// carries one: an import lands on one type.
const hasEngineTiers = computed(() => shown.value.some((bp) => bp.engine_tier != null))

// The engine-tier view: the strip counts, and its chips filter, by the engine's
// tier. Off by default and a reading aid alone: the tier column, the sort and
// the fold stay on the consensus tier.
const engineView = viewChoice('engineView')
const readsEngineTiers = computed(() => engineView.value && hasEngineTiers.value)

// The batch peaks where the engine's tier and the consensus tier differ. A
// batch peak with no engine tier is not among them: absence is not agreement,
// as on the API's `tier_disagrees`.
const onlyDisagreements = viewChoice('engineDisagrees')
const engineDisagrees = (row) => row.engine_tier != null && row.engine_tier !== row.consensus_tier

// Says which of the two tiers the chip is, and whether it agrees, as the
// sample ledger's does. Both tiers are named in words, not as the API's keys.
const engineTierTooltip = (row) => {
  const said = row.engine_claim ? ` - "${row.engine_claim}"` : ''
  return row.engine_tier === row.consensus_tier
    ? `The importing engine's own tier: ${tierName(row.engine_tier)} (agrees with the consensus)${said}`
    : `The importing engine's own tier: ${tierName(row.engine_tier)}, where the consensus of ` +
        `the members' measurements says ${tierName(row.consensus_tier)}${said}`
}

// The batch peak's own level (`level`, step 5.8c): read off its members' runs
// and the batch's isotope checks, with its two lines, and how the members
// split (`member_levels`) in its hover. Where no level of the batch stands -
// its level stage never ran, or the reading moved since - the level most of
// its members' runs stated (`member_level`, a tie reading as the weaker), as
// the hover says. Offered only where some batch peak shown carries one.
//
// Where the batch-level verdict on the row's present claim states a level of
// its own, the column shows the claim's in its place (readerLevel), and the
// hover says so and what the members' runs stated. A stale verdict, on a
// formula the consensus has left, speaks for no level of the row. The batch's
// own read serves a level-1 claim whole, window and all: the window decides
// which members it reaches, which the sample ledger shows.
const shownLevelOf = (row) => {
  const record = verdictFor(row)
  const own = batchLevelOf(row)
  return readerLevel({
    verdict: record && !verdicts.value.isStale(record, row) ? record : null,
    level: own?.level ?? row.member_level,
    evidence: own?.evidence ?? null,
    lift: own?.lift ?? null
  })
}
const hasMemberLevels = computed(() =>
  shown.value.some((bp) => isShownLevel(shownLevelOf(bp)?.level))
)
const levelOptions = computed(() =>
  levelFilterOptions(decorated.value.map((row) => row.shownLevel?.level))
)
// The hover of the Level cell's chip: the members' level as before, or the
// claim's, with what the members' runs stated beside it.
const shownLevelTooltip = (row) => {
  const level = row.shownLevel
  if (!level) return ''
  if (level.source !== 'claim') {
    if (batchLevelOf(row)) {
      return [batchLevelTooltip(row), 'No live batch-level verdict states a level'].join('\n')
    }
    return [
      memberLevelTooltip(row.member_level, row.member_levels),
      'Source: engine - no live batch-level verdict states a level'
    ].join('\n')
  }
  return [
    levelTooltip({
      level: level.level,
      evidence: level.evidence,
      lift: level.lift,
      source: 'claim'
    }),
    batchLevelOf(row)
      ? `The batch's own level: ${row.level}`
      : isLevel(row.member_level)
        ? `The members' runs stated ${row.member_level}`
        : "The members' runs stated no level for it"
  ].join('\n')
}

// The level an importing engine stated for the identity, beside the members'
// as its tier is beside the consensus tier.
const hasEngineLevels = computed(() => shown.value.some((bp) => isLevel(bp.engine_level)))

// Show > Engine readings: the engine's own tier and level as a second,
// outlined chip in the Tier and Level cells, where a batch peak carries them
// (owner's decision of 2026-11-22: the chip, not columns of their own).
const hasEngineReadings = computed(() => hasEngineTiers.value || hasEngineLevels.value)
const engineReadings = computed(() => engineView.value && hasEngineReadings.value)
// The Level column, where any batch peak shown carries a level the cell draws.
const hasLevelColumn = computed(
  () => hasMemberLevels.value || (engineReadings.value && hasEngineLevels.value)
)

// Says whose level the chip is, and whether it agrees with the members'.
const engineLevelTooltip = (row) => {
  const said = row.engine_claim ? ` - "${row.engine_claim}"` : ''
  const against = !isLevel(row.member_level)
    ? "The members' runs stated no level for it"
    : row.engine_level === row.member_level
      ? "It agrees with the level the members' runs stated"
      : `The members' runs stated ${row.member_level}`
  return [
    `The importing engine's own level: ${row.engine_level}${said}`,
    levelMeta(row.engine_level)?.description,
    // The engine's two lines about its level, as it wrote them.
    row.engine_evidence ? `Why: ${row.engine_evidence}` : null,
    row.engine_lift ? `Would lift it: ${row.engine_lift}` : null,
    against
  ]
    .filter(Boolean)
    .join('\n')
}

// --- The batch's groups -------------------------------------------------------------

// The rows each of the batch's readings spans, as the batch-peaks read serves
// them (`batch_groups`): a series, the channels of one neutral, a
// co-variation record's partners, a batch check's pair. One derivation on the
// server, so the ledger, its lens and the card count the same members; this
// pane only orders and labels them (lib/ledger/groups.js).
const groups = computed(() => ledger.value.groups ?? [])
const groupsIndex = computed(() => groupsByMember(groups.value))
const groupsOfRow = (row) => groupsIndex.value.get(row.batch_peak_id) ?? []

// The Ion cell's xN pill: the neutral is seen through N channels of the batch,
// counted on the served channel group, so the pill and the lens it opens
// count the same batch peaks.
const channelGroupOf = (row) => groupsOfRow(row).find((group) => group.kind === GROUP_CHANNELS)
const channelPillTooltip = (row) => {
  const group = channelGroupOf(row)
  const ions = (group?.members ?? []).map(
    (member) => member.mechanism || member.ion_formula || 'another channel'
  )
  return (
    `The batch reads ${group?.formula ?? 'this neutral'} through ${ions.length} channels: ` +
    `${ions.join(', ')}. Click to see them together`
  )
}

// The badge kinds that name other rows, and the group each opens.
const BADGE_GROUP_KINDS = Object.freeze({
  series: GROUP_SERIES,
  coVariation: GROUP_CO_VARIATION,
  facts: GROUP_CHECK_PAIR
})
const badgeGroup = (row, readingId) => {
  const kind = BADGE_GROUP_KINDS[readingId]
  if (!kind) return null
  return groupFor(groups.value, row.batch_peak_id, kind, row.series ?? null)
}

// The lens: the ledger narrowed to one group's rows, in the group's order,
// opened from a badge or the pill. Held in a store, so it survives the swap
// to a sample and back, and shown only while the batch it was opened on is
// the one listed.
const lenses = useLedgerLensStore()
const lensState = computed(() => {
  const state = lenses.lenses.batch
  return state && state.batchId === app.data.batch.focusedId ? state : null
})
// The group as the newest read serves it, where it still does.
const lensGroup = computed(() => {
  const state = lensState.value
  if (!state) return null
  return groups.value.find((group) => group.group_id === state.group.group_id) ?? state.group
})
function openLens(group) {
  if (!group) return
  lenses.open('batch', group, { batchId: app.data.batch.focusedId })
}
const closeLens = () => lenses.close('batch')
// The group's batch peaks into the batch chart, in place of what it plots.
const chart = useBatchChartSelection()
const plotLens = () =>
  chart.plotGroup((lensGroup.value?.members ?? []).map((member) => member.batch_peak_id))

// --- The batch's readings ---------------------------------------------------------

// Each offered only where some batch peak shown carries one, and each a
// reading aid alone: the tier column is what the table sorts, filters and
// folds on.
//
// A row a "Strip isotopologue rows" run read as a brighter compound's isotope
// line folds under that compound, as every isotopologue does; the compound
// carries the rows it took, which `onlyStripped` keeps.
const hasStrips = computed(() =>
  shown.value.some((bp) => bp.merged_isotopologue_of != null || bp.merged_isotopologues != null)
)
const onlyStripped = viewChoice('stripped')
const tookLines = (row) => strippedLinesCount(row.merged_isotopologues) > 0
// A batch peak whose two leading formulas part cleanly along intensity or
// acquisition time: two compounds may share its trace.
const hasBlends = computed(() => shown.value.some((bp) => blendOf(bp) != null))
const onlyBlends = viewChoice('blends')
// A batch peak whose trace holds a constant ratio to the batch's normaliser:
// an origin flag, likely background.
const hasFlatSeries = computed(() => shown.value.some((bp) => flatSeriesOf(bp) != null))
const onlyFlatSeries = viewChoice('flatSeries')
// Across the batch: the batch's readings of a row, one badge each, as the
// registry draws them (lib/evidence/marks.js). At most three show, the ones
// that hold the reading back first and the rest in the registry's order, and
// a "+n" says how many more. A click on it opens every badge of the row in a
// popover, each with its reading in words (BatchAcrossPopover). The
// ratio-stability badge opens the pairs behind its grade, and a badge whose
// reading names other rows opens them together, from the cell and from the
// popover alike.
const ACROSS_SHOWN = 3
const acrossMarks = (row) => {
  const marks = ledgerMarks(row).map((reading) => ({
    ...reading,
    holdsBack: markOf(reading.id).tone(reading.record) === TONE_HOLDS_BACK
  }))
  return [...marks.filter((m) => m.holdsBack), ...marks.filter((m) => !m.holdsBack)]
}
const acrossShown = (row) => acrossMarks(row).slice(0, ACROSS_SHOWN)
const acrossMore = (row) => acrossMarks(row).slice(ACROSS_SHOWN)
const acrossMoreTooltip = (row) => {
  const n = acrossMore(row).length
  return `${n} more reading${n === 1 ? '' : 's'} across the batch: click to see them all`
}
// The column's sort: the rows with the most that holds them back first, then
// the most badges; a row with none sorts last.
const acrossRankOf = (row) => {
  const marks = acrossMarks(row)
  if (!marks.length) return null
  return -(marks.filter((m) => m.holdsBack).length * 100 + marks.length)
}

// Show > one badge kind at a time: the rows that carry it in Across the
// batch. Offered for the kinds some row shown carries; the blend and the flat
// series have switches of their own.
const badgeKinds = computed(() =>
  BADGE_FILTER_KINDS.filter((id) => shown.value.some((bp) => ledgerMarks(bp, [id]).length))
)
const badgeFilter = viewChoice('badge')
const BADGE_OPTIONS = computed(() =>
  badgeKinds.value.map((id) => ({
    value: id,
    label: markOf(id).label,
    tooltip: 'Only the batch peaks carrying this badge in Across the batch'
  }))
)
const carriesBadge = (row, id) => ledgerMarks(row, [id]).length > 0

// The ledger's records with the ranks the columns sort on attached (the raw
// tier string sorts alphabetically, and "below_assignability" before
// "candidate" is not an ordering anyone asked for). An anchor a source role
// accounts for ranks and counts under its role, after the tiers, as the sample
// ledger's reagent and artifact rows do. A rank is null where there is nothing
// to rank, so those rows sort last in both directions.
const decorated = computed(() => {
  const index = byId.value
  return shown.value.map((batchPeak) => {
    // What a list calls the consensus formula, where a member matched it from
    // one, and what the target a member was matched to declares; `listingName`
    // is what the column sorts on, the target's name first.
    const listing = ledgerListing(batchPeak.reference_listing)
    const target = targetOf(batchPeak.reference_listing, listing)
    // The level the Level column draws: the claim's, else the members'.
    const shownLevel = shownLevelOf(batchPeak)
    return {
      ...batchPeak,
      tierRank: bucketRank(batchPeak.consensus_tier, batchPeak.consensus_role),
      // The consensus bucket, and the engine's, which the chips read in the
      // engine-tier view.
      bucket: bucketOf(batchPeak.consensus_tier, batchPeak.consensus_role),
      engineBucket: engineBucketOf(
        batchPeak.engine_tier,
        batchPeak.consensus_tier,
        batchPeak.consensus_role
      ),
      shownLevel,
      memberLevelRank: shownLevel ? levelRank(shownLevel.level) : null,
      acrossRank: acrossRankOf(batchPeak),
      verdictRank: verdictRank(verdictFor(batchPeak)),
      parentId: rootParentId(batchPeak, index),
      listing,
      target
    }
  })
})

// Isotopologues by the row they fold under, ordered by m/z among themselves -
// the order an isotopologue family reads in, M0 first and then M+1, M+2.
const childrenByParent = computed(() => {
  const families = new Map()
  for (const row of decorated.value) {
    if (!row.parentId) continue
    const family = families.get(row.parentId)
    if (family) family.push(row)
    else families.set(row.parentId, [row])
  }
  for (const family of families.values()) family.sort((a, b) => a.mz - b.mz)
  return families
})

// The rows that stand on their own: every anchor that is not an isotopologue of one
// the ledger holds. This is the population the tier strip counts and the table
// shows at top level, folded or not - the toggle decides only whether the
// isotopologues are drawn underneath.
const parents = computed(() => decorated.value.filter((row) => !row.parentId))

const familyHoldsBlend = (row) =>
  blendOf(row) != null ||
  (childrenByParent.value.get(row.batch_peak_id) ?? []).some((child) => blendOf(child) != null)
const isotopologueCount = (row) => childrenByParent.value.get(row.batch_peak_id)?.length ?? 0

// A stamp or a strip names a line's isotope only when the parent it names is
// the family the row folds into; one naming another family's peak says nothing
// about this one.
const inFamily = (row, parentId) => {
  if (!parentId || !row.parentId) return false
  const parent = byId.value.get(parentId)
  const root = parent ? (rootParentId(parent, byId.value) ?? parentId) : parentId
  return root === row.parentId
}
const stampOf = (row) => (inFamily(row, stampParentId(row)) ? row.stamped_isotopologue_of : null)
const stripOf = (row) => (inFamily(row, strippedParentId(row)) ? row.merged_isotopologue_of : null)

// An unfolded line is named by its substitution wherever a record names it -
// the batch's stamp, the strip, or the family's records of its lines - and by
// its offset from the M0 where none does (lib/ledger/rows.js).
const lineRecords = (row) => ({ stamp: stampOf(row), strip: stripOf(row) })
const childLabel = (row) =>
  batchLineLabel(row, byId.value.get(row.parentId) ?? null, lineRecords(row))
const lineName = (row) => batchLineName(row, byId.value.get(row.parentId) ?? null, lineRecords(row))
// What the line's label says on hover: the record that names it.
const lineTooltip = (row) => {
  const named = lineName(row)
  if (named?.source === 'stamp') return stampTooltip(named.record)
  if (named?.source === 'strip') return strippedTooltip(named.record)
  return row.consensus_formula || 'isotopologue'
}

// Fold isotopologues under their M0 by default: an isotopologue carries its family's
// formula, so unfolded it reads as a second row for one compound.
const showIsotopologues = viewChoice('isotopologues')
// The family's caret: how many lines fold under the row, and whether they
// are unfolded, one family at a time against Show's choice.
const families = useFamilyFold(showIsotopologues)
const familyOpen = (row) => families.isOpen(row.batch_peak_id)
const toggleFamily = (row) => families.toggle(row.batch_peak_id)

// --- Sorting and filtering --------------------------------------------------

// The sort is held per scope across the switch between the two ledgers, and a
// reload (lib/ledger/useLedgerSort.js). The formula column sorts and searches
// what it shows.
const {
  field: sortField,
  order: sortOrder,
  sort
} = useLedgerSort('batch', { consensus_formula: (row) => formulaOf(row) })

// The ledger's resting order: most confident first, best fit first within a
// tier. Also the tie-breaker under every other column.
const byConfidence = (a, b) =>
  a.tierRank - b.tierRank || (b.best_fit_score ?? -1) - (a.best_fit_score ?? -1)

// The search box, the tier chips (multi-select), the role rows, the verdict
// filter and the level's menu. In the engine-tier view the chips name the
// engine's buckets, while the tier column still shows the consensus. The
// search reads the formula the row shows, the names a list and a target give
// it, and its m/z and its isotope lines', within the window an imported batch
// result is matched at: the rows come from many runs, with no one run's mass
// gate to search at.
const { activeTiers, toggleTier, filters, verdictFilter, searchQuery, passes } = useLedgerFilters({
  bucketOf: (row) => (readsEngineTiers.value ? row.engineBucket : row.bucket),
  state: view,
  menus: { member_level: FilterMatchMode.EQUALS },
  // The menu filters on the level the column draws, a claim's included.
  valueOf: { member_level: (row) => row.shownLevel?.level ?? null },
  menuShown: { member_level: hasMemberLevels },
  view: readsEngineTiers,
  verdictOf: verdictFor,
  search: {
    texts: (row) => [
      formulaOf(row),
      row.listing ? listingName(row.listing) : null,
      row.target ? targetName(row.target) : null
    ],
    mzs: (row) => [
      row.mz,
      ...(childrenByParent.value.get(row.batch_peak_id) ?? []).map((line) => line.mz)
    ]
  },
  tolerancePpm: BATCH_SEARCH_PPM
})

// Show > Group by: none, compound (the channels of one neutral) or series, as
// the batch serves them. Collapsible group lines over their members; the
// rows in no group under an Ungrouped line at the end.
const GROUP_BY = [
  { value: 'none', label: 'None' },
  {
    value: 'compound',
    label: 'Compound',
    tooltip: 'One group line per neutral the batch reads through several channels'
  },
  {
    value: 'series',
    label: 'Series',
    tooltip: 'One group line per homologous series, over its batch peaks'
  }
]
// The group lines the reader folded, by key. Kept while the grouping stays.
const collapsed = reactive(new Set())
watch(
  () => view.groupBy,
  () => collapsed.clear()
)
const toggleGroupLine = (line) => {
  if (collapsed.has(line.groupKey)) collapsed.delete(line.groupKey)
  else collapsed.add(line.groupKey)
}

// The verdict filter's choices (single-select), as the sample scope's.
const VERDICT_FILTERS = [
  { value: 'all', label: 'All verdicts' },
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'unsure', label: 'Unsure' },
  { value: 'unverified', label: 'Unverified' }
]

// Parents are filtered, then ordered by the sorted column with confidence
// breaking ties. When unfolded, each parent's isotopologues are inserted right
// after it - a family is one block wherever its parent lands. The isotopologues
// ride with their parent rather than being filtered themselves: a family shares
// one formula and one tier. A probable blend is the exception, a reading of each
// anchor's own vote: an isotopologue can carry one its parent does not, so that
// filter keeps a parent whose family holds a blend.
const rows = computed(() => {
  // The lens lists its group, whatever the filters below would keep.
  if (lensGroup.value) return lensRows.value
  const disagreeing = onlyDisagreements.value && hasEngineTiers.value
  const stripped = onlyStripped.value && hasStrips.value
  const blended = onlyBlends.value && hasBlends.value
  const flat = onlyFlatSeries.value && hasFlatSeries.value
  // A kind no row shown carries any more filters nothing, rather than
  // emptying the table under a choice the menu no longer offers.
  const badge = badgeKinds.value.includes(badgeFilter.value) ? badgeFilter.value : null
  const visible = parents.value.filter(
    (row) =>
      passes(row) &&
      (!disagreeing || engineDisagrees(row)) &&
      (!stripped || tookLines(row)) &&
      (!blended || familyHoldsBlend(row)) &&
      (!flat || flatSeriesOf(row) != null) &&
      (!badge || carriesBadge(row, badge))
  )
  sort(visible, byConfidence)

  const result = []
  const withFamily = (parent) => {
    result.push(parent)
    if (familyOpen(parent)) result.push(...(childrenByParent.value.get(parent.batch_peak_id) ?? []))
  }
  const sets = view.groupBy === 'none' ? [] : batchGroupSets(groups.value, view.groupBy)
  if (!sets.length) {
    visible.forEach(withFamily)
    return result
  }
  // Group by: a group line over its members, each member with its lines.
  for (const section of groupSections(visible, sets, (row) => row.batch_peak_id)) {
    const isCollapsed = collapsed.has(section.key)
    result.push({
      batch_peak_id: `group:${section.key}`,
      ledgerKind: 'group',
      groupKey: section.key,
      groupName: section.name,
      groupCount: `${section.size} batch peak${section.size === 1 ? '' : 's'}`,
      collapsed: isCollapsed
    })
    if (isCollapsed) continue
    section.rows.forEach(withFamily)
    for (const { row, under } of section.references) {
      result.push({
        batch_peak_id: `reference:${section.key}:${row.batch_peak_id}`,
        ledgerKind: 'reference',
        pseudoMz: row.mz,
        pseudoFormula: formulaOf(row),
        pseudoIon: row.consensus_ion_formula,
        pseudoNote: `listed under ${under}`
      })
    }
  }
  return result
})

// Under the lens: the group's rows in the group's order, whatever the tier
// chips, the search or the Show filters hide - the lens lifts them while it
// is open - each labelled by its step or channel. A series step with no batch
// peak is a greyed gap; a member the ledger lists no row for (the other
// instrument type's) a greyed line. A member may be an isotope line - the line
// a halogen lock names folds under its compound - and is listed as the line it
// is. A sort picked under the lens orders the members by its column, the gaps
// after them.
const decoratedById = computed(
  () => new Map(decorated.value.map((row) => [row.batch_peak_id, row]))
)
const lensRowOf = (member) => decoratedById.value.get(member.batch_peak_id) ?? null
const lensRows = computed(() => {
  const group = lensGroup.value
  if (!group) return []
  const entries = lensEntries(group, lensRowOf)
  const members = entries
    .filter((entry) => entry.entry === ENTRY_MEMBER)
    .map((entry) => ({ ...entry.row, lensLabel: entry.label }))
  const pseudo = (entry) =>
    entry.entry === ENTRY_GAP
      ? {
          batch_peak_id: `lens:${entry.key}`,
          ledgerKind: 'gap',
          lensLabel: entry.label,
          pseudoFormula: entry.formula,
          pseudoNote: 'no batch peak'
        }
      : {
          batch_peak_id: `lens:${entry.key}`,
          ledgerKind: 'absent',
          lensLabel: entry.label,
          pseudoMz: entry.member.mz,
          pseudoFormula: entry.member.formula,
          pseudoIon: entry.member.ion_formula,
          pseudoNote: 'not in this ledger'
        }
  // An unfolded family lists its lines under its member, but not a line that
  // is a member of its own: that one is listed where the group places it.
  const memberIds = new Set(members.map((row) => row.batch_peak_id))
  const result = []
  const withFamily = (row) => {
    result.push(row)
    if (!familyOpen(row)) return
    const lines = childrenByParent.value.get(row.batch_peak_id) ?? []
    result.push(...lines.filter((line) => !memberIds.has(line.batch_peak_id)))
  }
  if (lensState.value?.sorted && sortField.value) {
    sort(members, byConfidence)
    members.forEach(withFamily)
    result.push(...entries.filter((entry) => entry.entry !== ENTRY_MEMBER).map(pseudo))
    return result
  }
  let next = 0
  for (const entry of entries) {
    if (entry.entry === ENTRY_MEMBER) withFamily(members[next++])
    else result.push(pseudo(entry))
  }
  return result
})
// A sort picked under the lens is the reader's: the members follow it.
watch([sortField, sortOrder], () => {
  if (lensGroup.value) lenses.sortedUnder('batch')
})
// The lens bar's line and its label column.
const lens = computed(() => {
  const group = lensGroup.value
  if (!group) return null
  const entries = lensEntries(group, lensRowOf)
  return { title: lensTitle(group, entries), column: lensColumnLabel(group), plot: true }
})

// --- Popovers -------------------------------------------------------------------

// One verdict popover and one continuity popover for the table, each pointed at
// the row whose cell opened it, read live from the ledger. Both cells stop their
// click, so each opener closes the other popover itself.
const findDecorated = (id) => decorated.value.find((row) => row.batch_peak_id === id)
const idOf = (row) => row.batch_peak_id
// Each opener closes the others: the table's cell popovers show one at a time.
const hideOthers = (keep) =>
  [verdictMenu, continuityMenu, levelMenu, acrossMenu]
    .filter((menu) => menu !== keep)
    .forEach((menu) => menu.value?.hide())
const {
  menu: verdictMenu,
  target: verdictTarget,
  open: openVerdict
} = useRowPopover(findDecorated, idOf, { beforeOpen: () => hideOthers(verdictMenu) })
const {
  menu: continuityMenu,
  target: continuityTarget,
  open: openContinuity
} = useRowPopover(findDecorated, idOf, { beforeOpen: () => hideOthers(continuityMenu) })
const {
  menu: levelMenu,
  target: levelTarget,
  open: openLevelPopover
} = useRowPopover(findDecorated, idOf, { beforeOpen: () => hideOthers(levelMenu) })
const {
  menu: acrossMenu,
  target: acrossTarget,
  open: openAcrossPopover
} = useRowPopover(findDecorated, idOf, { beforeOpen: () => hideOthers(acrossMenu) })

// --- Across the batch's "+n" ---------------------------------------------------
// What a badge's click does, the same from the cell and from the popover: the
// ratio-stability badge opens the pairs behind its grade, a badge whose
// reading names other rows opens them together, any other does nothing.
function acrossAction(row, reading) {
  if (reading.id === 'ratio') {
    return { kind: 'continuity', label: 'Ratio stability: open the pairs behind the grade' }
  }
  const group = badgeGroup(row, reading.id)
  if (!group) return null
  return { kind: 'lens', group, label: `${markOf(reading.id).label}: see its rows together` }
}
const acrossReadings = (row) =>
  acrossMarks(row).map((reading) => ({ ...reading, action: acrossAction(row, reading) }))
// The "+n" that opened the popover: where the pairs open when the popover's
// ratio badge is clicked (the popover is gone by then), and where focus
// returns when it closes.
let acrossOpener = null
function openAcross(event, row) {
  acrossOpener = event.currentTarget
  openAcrossPopover(event, row)
}
function actFromAcross(reading) {
  const row = acrossTarget.value
  if (!row) return
  acrossMenu.value?.hide()
  if (reading.action?.kind === 'continuity') {
    openContinuity({ currentTarget: acrossOpener, target: acrossOpener }, row)
  } else if (reading.action?.kind === 'lens') {
    openLens(reading.action.group)
  }
}
// Focus goes back to "+n" when the popover closes, unless the reader has
// moved it somewhere else (an outside click on another control keeps it).
function returnAcrossFocus() {
  const opener = acrossOpener
  if (!opener) return
  const active = document.activeElement
  if (active && active !== document.body && !active.closest?.('.across-popover')) return
  opener.focus()
}

// --- Where a row's level comes from ------------------------------------------
// The Level chip opens the members whose run states it (LedgerLevelSources),
// under the batch's own level where it states one:
// one read per batch peak, asked on the click, never on the ledger's list read
// (MAS-1160). An earlier run's snapshot keeps no member levels, so it asks
// nothing. The answer is kept for the row it was asked for; a reply that
// arrives after the popover moved to another row is dropped.
const levelSources = reactive({ id: null, body: null, loading: false, error: null })
const viewingSnapshot = computed(() => Boolean(runs.value?.viewingId))
async function loadLevelSources(row) {
  const batchId = app.data.batch.focusedId
  Object.assign(levelSources, { id: row.batch_peak_id, body: null, loading: false, error: null })
  if (!batchId || viewingSnapshot.value) return
  levelSources.loading = true
  try {
    const response = await api.http.get(
      `/batch-peaks/batch/${batchId}/levels/${row.batch_peak_id}`,
      { type: 'load_batch_peak_level_sources', errors: 'inline' }
    )
    if (levelSources.id !== row.batch_peak_id) return
    levelSources.body = response?.data ?? null
  } catch (err) {
    if (levelSources.id !== row.batch_peak_id) return
    levelSources.error = getApiErrorMessage(err, "Could not read the members' levels.")
  } finally {
    if (levelSources.id === row.batch_peak_id) levelSources.loading = false
  }
}
function openLevelSources(event, row) {
  // A second click on the open row's chip closes it and asks nothing. Any
  // other click opens it and reads again, the same row's included: the
  // popover may have closed on an Assign batch that moved the members' levels,
  // and batch peak ids stay the same across a re-fold.
  const closing =
    Boolean(levelMenu.value?.visible) && levelTarget.value?.batch_peak_id === row.batch_peak_id
  openLevelPopover(event, row)
  if (!closing) loadLevelSources(row)
}
// The claim the row shows in place of the members' level, for the popover.
const claimOf = (row) => (row?.shownLevel?.source === 'claim' ? row.shownLevel : null)
// "Open a sample": the member it names, in the sample's ledger with the peak
// focused and the inspector card stepping through that level's members.
function openLevelMember(member, members, level) {
  const row = levelTarget.value
  if (!row) return
  levelMenu.value?.hide()
  jump.openMember(row, member, members, level)
}
function assignFromLevel() {
  levelMenu.value?.hide()
  assign.assign()
}

// --- Selection --------------------------------------------------------------

// How many rows the last bulk write wanted, when that was more than the cap
// allows; 0 when it fit. Counting the rows rather than raising a flag is what
// lets the notice say how much of the ledger was left out.
const overflowFrom = ref(0)

// Set instead when a single row was refused because the selection was already
// full. A row click hands over the selection with the clicked row appended, so
// it arrives exactly one over the cap and the slice drops the very row that was
// clicked: a refusal, not a select-all whose tail was cut off.
const selectionAtCapacity = ref(false)

// The rows "all" means: filtered, sorted and folded as displayed, less the
// isotopologue lines. A line is its family's species, and a sub-row carries no
// box (docs/dev/assignment_ledger_and_card.md, section 3.3): the family's row
// is what plots it.
// A group line, a gap or another row that stands for no batch peak is no row
// to select either.
const isLine = (row) => Boolean(row?.parentId || row?.ledgerKind)
const selectableRows = () => rows.value.filter((row) => !isLine(row))

/**
 * The single write into the ledger selection, capped.
 *
 * Every route into the selection lands here - the header checkbox, Ctrl+A, a
 * shift-click range, and an ordinary row click - because each of the first
 * three hands over the whole filtered row set in one go.
 *
 * Always assigns a fresh array: the header checkbox hands over the table's own
 * live `processedData`, which must not be truncated in place.
 */
const applySelection = (selection) => {
  // Shift+Space hands over a bare row rather than an array of one when the
  // range it would select collapses onto the row already focused. A click on
  // a line, or a range across lines, hands them over too: they are not kept.
  const wanted = (Array.isArray(selection) ? selection : [selection]).filter(
    (row) => row && !isLine(row)
  )
  const capped = wanted.slice(0, MAX_SELECTED_BATCH_PEAKS)
  const dropped = wanted.length - capped.length
  // One row over a selection that was already full is a click the cap refused;
  // more than that is a bulk gesture whose tail did not fit.
  const refused = dropped === 1 && ledger.value.selected.length >= MAX_SELECTED_BATCH_PEAKS
  selectionAtCapacity.value = refused
  overflowFrom.value = dropped && !refused ? wanted.length : 0
  ledger.value.selected = capped
}

/**
 * Whether the header checkbox reads as checked.
 *
 * Taken over from the table so the checkbox stays usable at the cap: left to
 * itself it looks for every filtered row in the selection, never finds them
 * all, and so offers only to re-select the same rows. It is the same membership
 * test over the rows "select all" would write - a count would read as checked
 * whenever the selection happened to be as large as the filtered set, even with
 * no row in common. Bounded by the cap, so the test stays cheap.
 */
const allSelected = computed(() => {
  const selectable = selectableRows()
  if (!selectable.length) return false
  const held = new Set(ledger.value.selected.map((row) => row.batch_peak_id))
  return selectable.slice(0, MAX_SELECTED_BATCH_PEAKS).every((row) => held.has(row.batch_peak_id))
})

/** What to tell the user about the size of their selection, or null. */
const selectionNotice = computed(() => {
  if (overflowFrom.value) {
    return (
      `At most ${MAX_SELECTED_BATCH_PEAKS} batch peaks are plotted at once, so ` +
      `${MAX_SELECTED_BATCH_PEAKS} of the ${overflowFrom.value.toLocaleString('en-US')} ` +
      `matching rows are selected. Narrow the ledger - by tier or formula - to choose which.`
    )
  }
  if (selectionAtCapacity.value) {
    return (
      `The selection is full at ${MAX_SELECTED_BATCH_PEAKS} batch peaks, as many as the chart ` +
      `plots at once. Deselect a row to make room for another.`
    )
  }
  return null
})

const onSelectAllChange = (event) => applySelection(event.checked ? selectableRows() : [])

// Ctrl+A selects all filtered rows (the virtual scroller only holds visible rows).
const onKeyDown = (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'a') {
    event.preventDefault()
    applySelection(selectableRows())
  }
}

// The notices belong to the gesture, so they clear when the toggle moves.
watch(showIsotopologues, () => {
  overflowFrom.value = 0
  selectionAtCapacity.value = false
})

// No isotopologue line is selected.
//
// A selection is what the chart plots, and a line carries no box to tick or
// untick: one left in the selection draws a trace with no ticked row behind it,
// and spends the cap on a row the user cannot release. Written as an invariant
// rather than as a reaction to a gesture, because a selection made before the
// lines lost their box - the store's selection outlives the pane - can still
// hold one.
watch(
  [decorated, () => ledger.value.selected],
  () => {
    if (!ledger.value.selected.length) return
    const isotopologues = new Set(
      decorated.value.filter((row) => row.parentId).map((row) => row.batch_peak_id)
    )
    if (!isotopologues.size) return
    const kept = ledger.value.selected.filter((row) => !isotopologues.has(row.batch_peak_id))
    if (kept.length !== ledger.value.selected.length) ledger.value.selected = kept
  },
  { immediate: true }
)

// --- Tier strip -------------------------------------------------------------

// Counted over `parents`, the same population the table shows at top level and
// the header counts: an isotopologue carries its family's formula and tier, so
// counting it counts one species twice.
const tierChips = computed(() => {
  const counts = {}
  for (const row of parents.value) {
    const bucket = readsEngineTiers.value ? row.engineBucket : row.bucket
    counts[bucket] = (counts[bucket] ?? 0) + 1
  }
  return ledgerTierChips(counts, {
    engineView: readsEngineTiers.value,
    roles: view.roles,
    active: activeTiers.value
  })
})

// The ledger's count, in its header line: species, not anchors, so it matches
// the tier strip and the rows at top level. The shell's breadcrumb names the
// batch.
const count = computed(() => {
  const n = parents.value.length
  return `${n.toLocaleString('en-US')} batch peak${n === 1 ? '' : 's'}`
})
const countTooltip = computed(() => {
  const folded = shown.value.length - parents.value.length
  return folded
    ? `${parents.value.length} species; ${folded} isotopologue ` +
        `peak${folded === 1 ? '' : 's'} shown under their main peak`
    : null
})

// --- Header tooltips ------------------------------------------------------------

// What the intensity column reports: one number per species out of a per-sample
// matrix, from the brightest sample. The unit is the batch peak's own - heights
// on an Orbitrap, areas on a TOF - so it is read off the rows.
const INTENSITY_UNITS = {
  sum_peak_heights: 'summed peak height',
  sum_peak_areas: 'summed peak area'
}
const intensityTooltip = computed(() => {
  const unit = INTENSITY_UNITS[shown.value.find((bp) => bp.intensity_variable)?.intensity_variable]
  return (
    "Max intensity: the brightest sample's, the highest this species reaches in any sample " +
    'of the batch' +
    (unit ? ` (${unit})` : '')
  )
})

// One line per column whose number is a property of the batch peak rather than
// of a single peak - the columns a reader arriving from the sample ledger, where
// the same names mean one peak's values, reads wrong at first.
const HEADER_TOOLTIPS = {
  mz:
    "Anchor m/z of the batch peak: the m/z bin every sample's peak of this species folds " +
    'into, set by the first peak seen and never redrawn. Click it to open the peak in its ' +
    'brightest sample',
  formula:
    "Consensus formula: an evidence-weighted vote over the members' per-sample assignments, " +
    'so a bright, well-fitting member outweighs weak ones. Under it, what a reference list ' +
    'or a target names it',
  ion:
    'The ion the batch peak is: the consensus formula with its adduct and charge. Two ' +
    'channels of one compound are two batch peaks, told apart here',
  tier:
    "Consensus tier: an evidence-weighted vote over the members' per-sample tiers, " +
    'assigned only when a weighted majority reach it',
  level:
    "Identification level the batch states for the consensus formula: what its members' runs " +
    "stated, read under the batch's isotope checks, by majority, a tie reading as the weaker " +
    'level. What may be stated of it, from 5 (a measured mass) to 1 (a confirmed structure). ' +
    "Where the batch states none, the level most members' runs stated; no tier moves on it",
  samples: "Samples: how many of the batch's samples this species is seen in",
  across:
    "Across the batch: what the batch's own steps read of this species - a series, " +
    'co-variation, isotope lines, ratio stability, the batch facts, a blend, a flat ' +
    'series - one badge each, the ones that hold it back first',
  verdict:
    'Batch-level verdict: one judgment per species, covering every sample without a ' +
    'verdict of its own'
}
const headers = computed(() => ({ ...HEADER_TOOLTIPS, intensity: intensityTooltip.value }))

const FIELDS = {
  mz: 'mz',
  intensity: 'max_intensity',
  formula: 'consensus_formula',
  ion: 'consensus_ion_formula',
  level: 'member_level',
  levelSort: 'memberLevelRank'
}

// Samples: the count of the batch's samples the species is seen in, alone. A
// batch peak's partition (its ionization mode and instrument type) holds only
// some of the batch's samples, and none added since the last fold, so the
// batch's sample list is no denominator for it, and the read serves none.
const samplesLabel = (row) => (row.n_present == null ? '' : `${row.n_present}`)

// The tier chip's cap mark: what the batch's own rules hold the tier down on,
// named as a run's reasons are.
const tierCap = (row) => {
  const reasons = holdingReasons(row.tier_reasons)
  if (!reasons.length) return null
  return {
    label: reasons.map((reason) => reason.label).join(', '),
    tooltip: holdingTooltip(row.tier_reasons, row.consensus_tier)
  }
}

// The bars of the intensity column are drawn over every batch peak shown.
const intensities = computed(() => shown.value.map((bp) => bp.max_intensity))

// The m/z and the row's action open the batch peak in its brightest sample,
// with the peak in focus in the inspector.
const openBrightest = (row) => {
  if (jump.pendingId == null && row.n_present) jump.jumpToBrightest(row)
}

// Back from a sample this ledger opened (stores/batchLedgerReturn.js): the
// row it was opened from, selected and scrolled into view. The filters, the
// sort and the rest of the selection are as the reader left them, held in
// stores that outlive the pane. A folded isotopologue line comes back as its
// family's row; a row the filters now hide is not brought back. Waits for the
// rows when they are still loading, and is done after one try.
const ledgerReturn = useBatchLedgerReturn()
const ledgerRef = ref()
watch(
  [() => ledgerReturn.reveal, () => rows.value.length, () => ledger.value.pending],
  ([batchPeakId]) => {
    if (batchPeakId == null || ledger.value.pending || !ledger.value.list.length) return
    ledgerReturn.reveal = null
    const parentId = decoratedById.value.get(batchPeakId)?.parentId
    let index = rows.value.findIndex((row) => row.batch_peak_id === batchPeakId)
    if (index < 0 && parentId) index = rows.value.findIndex((row) => row.batch_peak_id === parentId)
    if (index < 0) return
    const row = rows.value[index]
    if (!isLine(row) && !ledger.value.selected.some((s) => s.batch_peak_id === row.batch_peak_id)) {
      applySelection([...ledger.value.selected, row])
    }
    nextTick(() =>
      scrollVirtualRowIntoView(ledgerRef.value?.table, index, { itemSize: LEDGER_ROW_HEIGHT })
    )
  },
  { immediate: true }
)

// Whatever was in flight, went wrong, or was selected belonged to the previous
// batch. The selection itself is reset by the ledger's own reload.
watch(
  () => app.data.batch.focusedId,
  () => {
    assign.reset()
    overflowFrom.value = 0
    selectionAtCapacity.value = false
  }
)
</script>

<template>
  <AssignmentLedger
    ref="ledgerRef"
    scope="batch"
    label="Batch peaks"
    icon="pi ph ph-atom"
    :count="ledger.list.length ? count : ''"
    :countTooltip="countTooltip"
    :panelPt="
      app.ui.help.right(
        `
        <h1>Batch Peak Ledger</h1>
        <p>
        Cross-sample m/z anchors for the batch: each row is a species seen
        across samples, with its consensus formula and tier, the number of
        samples it appears in, and the highest intensity it reaches in any of
        them. Isotopologue peaks are folded under their main peak;
        <b>Show</b> in the filter bar unfolds them. The search box finds a
        formula, a listed name or an m/z, within 5 ppm.
        </p>
        <p>
        Where the members' runs state one, the <b>Level</b> column gives the
        identification level most of them stated for the consensus formula.
        </p>
        <p>
        The rows selected here are what the Assignments chart plots &mdash;
        Ctrl+A selects all filtered rows, up to the
        ${MAX_SELECTED_BATCH_PEAKS} the chart draws at once. Filter first to
        choose which ones. <b>Assign batch</b> in the bar above
        runs the batch's pass. Focus a sample to switch to its per-sample
        assignment ledger.
        </p>`,
        { doc: app.ui.help.docUrl('how-it-works/peak-assignment/#batch-peaks') }
      )
    "
    :pending="ledger.pending"
    :error="ledger.error"
    errorFallback="Could not load the batch peaks."
    :onRetry="() => ledger.load('retry')"
    :rows="rows"
    :intensities="intensities"
    intensityLabel="Max intensity"
    mzAction="Open the brightest sample with this peak in focus"
    v-model:sortField="sortField"
    v-model:sortOrder="sortOrder"
    :fields="FIELDS"
    :levelOptions="levelOptions"
    :headers="headers"
    :strip="ledger.list.length > 0"
    v-model:search="searchQuery"
    searchTooltip="A formula, a listed name, or an m/z: an m/z matches within 5 ppm, the window an imported batch result is matched to batch peaks at"
    :chips="tierChips"
    :activeTiers="activeTiers"
    :engineView="readsEngineTiers"
    engineViewTooltip="The chips count and filter by the importing engine's own tier. The tier column and the sort stay on the consensus."
    :viewMenuTooltip="
      hasEngineTiers
        ? 'Show: isotopologue lines, roles, the batch\'s flags, the engine\'s tiers, the verdict filter, Group by'
        : 'Show: isotopologue lines, roles, the batch\'s flags, the verdict filter, Group by'
    "
    :viewMenuPt="
      app.ui.help.top(
        `
        <h1>Show</h1>
        <p>
        What the ledger's rows show, rather than what it is reading.
        <b>Isotopologue lines</b> unfolds each species' isotopologue peaks -
        folded under the caret and line count beside the formula by default -
        as indented rows under their main peak (M0). An isotopologue peak
        carries its family's formula, so left in the list it reads as a
        second species. <b>Roles</b> shows the batch peaks the reagent and
        the artifacts account for.
        </p>
        <p>
        The link is derived: a batch peak is an m/z anchor and carries no
        family of its own, so an isotopologue is one whose per-sample
        assignments agree, across the batch, that it belongs to another
        anchor's compound, or one <b>Stamp isotopologues</b> stamped as a
        diagnostic line of it. A row <b>Strip isotopologue rows</b> read as
        another compound's isotope line folds under it too, outlined.
        A line is named by its substitution wherever the stamp, the strip or
        the family's own records name it, and by its offset (M+1) where
        none does. <b>Stripped rows</b> keeps the compounds that took such
        rows.
        </p>
        <p>
        A batch peak whose samples split between two formulas - one on
        the bright samples and the other on the dim ones, or each in
        blocks of the batch's samples - may be two compounds sharing one
        peak. It carries an intersect badge in <b>Across the batch</b>, the chart
        names it by both formulas, and <b>Probable blends</b> keeps only
        those. Its consensus and tier stay as the samples voted.
        </p>
        <p>
        <b>Read co-variation</b> also reads each compound's trace
        against the batch's reagent ions, or the samples' total ion
        current where the batch has none. A trace that holds a constant
        ratio to them through the batch likely comes from the source or
        the inlet rather than from what was sampled: it carries a level
        badge in <b>Across the batch</b>, which also names the ion where the
        source's own list holds it. <b>Flat series</b> keeps only those,
        so background can be filtered out. The flag says where an ion
        comes from, not whether its formula is right, and moves no tier.
        A batch that holds still throughout is not read so.
        </p>
        <p>
        After an import that carried the engine's own tiers,
        <b>Engine tier</b> makes the tier chips count and filter by that
        engine's tier, with <b>not stated</b> for the formulas it gave
        none, and <b>Engine disagrees</b> keeps the batch peaks where its
        tier and the consensus differ. The tier column and the sort stay
        on the consensus.
        </p>
        <p>
        <b>Carrying the badge</b> keeps the batch peaks with one kind of
        badge in <b>Across the batch</b>, one kind at a time; click it again to
        drop it. <b>Verdict</b> narrows the table to one batch-level verdict.
        </p>
        <p>
        <b>Group by</b> sets the rows under collapsible group lines, by
        compound (the channels of one neutral) or by series, the rows in no
        group under <b>Ungrouped</b>. A series, co-variation or batch facts
        badge, or the &times;N pill beside an ion, shows the rows it is about
        together until &times; or Escape closes the lens.
        </p>
        <p>
        <b>Level</b>, once a row carries one, narrows the table to one
        identification level and sorts the rows by it, as the <b>Level</b>
        column's header does. When the ledger is narrow, that column gives way
        and each row shows its level beside its tier; this is then where its
        filter and sort are.
        </p>
        <p>
        The search, the tier chips and every choice here but the level filter are kept for this
        ledger: they hold when you open a sample and come back, and after a
        reload. <b>Export ledger (CSV)</b> and <b>Write listed names in
        full</b> are under the <b>...</b> menu beside this one.
        </p>`,
        { doc: app.ui.help.docUrl('how-it-works/peak-assignment/#batch-peaks') }
      )
    "
    :engineReadings="engineReadings"
    :engineTierTooltip="engineTierTooltip"
    :hasLevels="hasLevelColumn"
    :tierCap="tierCap"
    :familySize="isotopologueCount"
    :familyOpen="familyOpen"
    :verdictCell="verdictCell"
    :verdictHelp="{
      title: 'Batch-Level Verdicts',
      helpKey: 'batch-peak-verdicts',
      doc: app.ui.help.docUrl('how-it-works/peak-assignment/#batch-level-verdicts')
    }"
    dataKey="batch_peak_id"
    :selection="ledger.selected"
    @update:selection="applySelection"
    :selectAll="allSelected"
    @select-all-change="onSelectAllChange"
    v-model:filters="filters"
    selectionMode="multiple"
    :metaKeySelection="false"
    @keydown="onKeyDown"
    resizableColumns
    :pt="
      app.ui.help.top(
        { title: 'Batch Peaks', helpKey: 'batch-peaks' },
        { doc: app.ui.help.docUrl('how-it-works/peak-assignment/#batch-peaks') }
      )
    "
    :lens="lens"
    @toggle-tier="toggleTier"
    @open-verdict="openVerdict"
    @open-mz="openBrightest"
    @toggle-family="toggleFamily"
    @toggle-group="toggleGroupLine"
    @close-lens="closeLens"
    @plot-lens="plotLens"
  >
    <template #above-strip>
      <!-- A launch that was refused or failed reports itself here rather than in
           a toast that has scrolled away by the time the user looks up. -->
      <Message
        v-if="assign.launchError"
        :severity="assign.launchRefused ? 'warn' : 'error'"
        closable
        @close="assign.launchError = null"
      >
        {{ assign.launchError }}
      </Message>

      <!-- Said here, where the gesture was made, rather than left for the user
           to infer from a chart that draws fewer traces than the ledger shows
           ticked. Informational: the selection just has a size. -->
      <Message v-if="selectionNotice" severity="secondary" icon="pi pi-exclamation-triangle">
        {{ selectionNotice }}
      </Message>

      <!-- An earlier run's ledger, from its snapshot. Said here so a formula
           that differs from what a sample's inspector shows reads as history
           rather than as a disagreement, and so the withheld verdict cells
           have their reason. -->
      <Message v-if="!runs.viewingCurrent" severity="info" icon="pi ph ph-clock-counter-clockwise">
        Showing the ledger as {{ viewedRunLabel }} left it, read-only. Verdicts and curation act on
        the current run; Back to current, in the bar above, returns to it to judge.
      </Message>

      <!-- Only on a batch of both instrument types: which one's batch peaks
           the table lists and the chart draws. -->
      <div v-if="instrumentOptions.length > 1" class="instrument-partition">
        <SelectButton
          v-model="instrumentShown"
          :options="instrumentOptions"
          optionLabel="label"
          optionValue="value"
          :allowEmpty="false"
          aria-label="Instrument type"
          v-tooltip.top="
            'This batch holds files of two instrument types. An Orbitrap\'s intensities are ' +
            'heights and a TOF\'s areas, so each has batch peaks of its own, and the chart ' +
            'draws one at a time.'
          "
        />
      </div>
    </template>

    <template #view-menu>
      <div class="menu-section">
        <span class="menu-heading">Rows</span>
        <LedgerViewToggle
          v-model="showIsotopologues"
          id="unfold-batch-iso"
          label="Isotopologue lines"
          tooltip="Show isotopologue peaks as indented rows under their main peak"
          autofocus
        />
        <LedgerViewToggle
          v-model="view.roles"
          id="batch-show-roles"
          label="Roles"
          tooltip="Show the batch peaks the reagent and the artifacts account for, which are not the samples' compounds"
        />
        <LedgerViewToggle
          v-if="hasStrips"
          v-model="onlyStripped"
          id="batch-stripped-rows"
          label="Stripped rows"
          tooltip="Only the compounds that took rows of the ledger as their isotope lines; unfold the isotopologues to see the rows under them"
        />
      </div>
      <div v-if="hasBlends || hasFlatSeries || badgeKinds.length" class="menu-section">
        <span class="menu-heading">The batch's flags</span>
        <LedgerViewToggle
          v-if="hasBlends"
          v-model="onlyBlends"
          id="batch-probable-blends"
          label="Probable blends"
          tooltip="Only the batch peaks whose samples split between two formulas along intensity or acquisition time"
        />
        <LedgerViewToggle
          v-if="hasFlatSeries"
          v-model="onlyFlatSeries"
          id="batch-flat-series"
          label="Flat series"
          tooltip="Only the batch peaks whose trace holds a constant ratio to the batch's reagent ions or total ion current: likely background. No tier moves on it"
        />
        <LedgerMenuChoice
          v-if="badgeKinds.length"
          v-model="badgeFilter"
          class="badge-filter"
          label="Carrying the badge"
          ariaLabel="Filter by a badge the batch peak carries"
          :options="BADGE_OPTIONS"
          clearable
        />
      </div>
      <div v-if="hasEngineReadings" class="menu-section">
        <span class="menu-heading">Engine readings</span>
        <LedgerViewToggle
          v-model="engineView"
          id="batch-engine-tier-view"
          label="Engine tier and level"
          tooltip="Show the importing engine's own tier and level as a second, outlined chip in the Tier and Level cells, and count and filter the tier chips by its tier"
        />
        <LedgerViewToggle
          v-if="hasEngineTiers"
          v-model="onlyDisagreements"
          id="batch-engine-disagrees"
          label="Engine disagrees"
          tooltip="Only the batch peaks where the importing engine's tier differs from the consensus"
        />
      </div>
      <LedgerMenuChoice
        v-model="verdictFilter"
        class="verdict-filter"
        label="Verdict"
        ariaLabel="Filter by batch-level verdict"
        :options="VERDICT_FILTERS"
      />
      <LedgerMenuChoice
        v-model="view.groupBy"
        class="group-by"
        label="Group by"
        ariaLabel="Group the rows by"
        :options="GROUP_BY"
      />
    </template>

    <template #overflow>
      <!-- The whole ledger as a CSV: every batch peak with every sample's
           member, one row per member. A background task; the browser
           downloads the file when it is ready. -->
      <Button
        class="export-ledger"
        label="Export ledger (CSV)"
        icon="pi ph ph-download-simple"
        size="small"
        text
        severity="secondary"
        :disabled="!parents.length || assign.exporting"
        :loading="assign.exporting"
        v-tooltip.top="
          'Download the whole ledger as CSV: one row per member peak, with the batch peak it folded into'
        "
        @click="assign.exportLedger()"
      />
    </template>

    <template #empty>
      <!-- The empty ledger carries Assign batch, and the bar hides its own
           copy then (BatchAssignControl), so one button is on screen. A
           ledger whose rows the filters hide says so instead. -->
      <BaseEmptyState
        v-if="assign.ledgerOffersAssign"
        icon="pi ph ph-magic-wand"
        title="Not assigned"
        hint="Assign the batch to fold its samples into batch peaks, search what the known compositions leave, and cross-check it across the batch."
      >
        <template #actions>
          <span v-tooltip.top="assign.blockedReason">
            <Button
              class="empty-assign"
              label="Assign batch"
              icon="pi ph ph-magic-wand"
              size="small"
              :loading="assign.launching"
              :disabled="Boolean(assign.blockedReason)"
              @click="assign.assign()"
            />
          </span>
        </template>
      </BaseEmptyState>
      <BaseEmptyState v-else-if="!ledger.list.length" title="No batch peaks yet" />
      <BaseEmptyState v-else title="No batch peaks match these filters" />
    </template>

    <template #lead-column>
      <Column selectionMode="multiple" style="width: 3rem" />
    </template>

    <!-- An unfolded isotopologue says what it is rather than repeating its
         family's formula, which is the whole reason it was folded: its
         substitution, from the record that names it - in italics where the
         stamping predicted it rather than the samples' runs finding it, and
         outlined where the batch stripped the row to the family's line. -->
    <template #line-label="{ data }">
      <span class="child-cell">
        <span class="child-caret">&#8627;</span>
        <span
          class="child-label"
          :class="{
            stamped: lineName(data)?.source === 'stamp',
            predicted: lineName(data)?.source === 'stamp' && isPredicted(lineName(data).record),
            stripped: lineName(data)?.source === 'strip'
          }"
          :data-testid="
            lineName(data)?.source === 'stamp'
              ? 'stamp-tag'
              : lineName(data)?.source === 'strip'
                ? 'strip-tag'
                : null
          "
          v-tooltip.top="lineTooltip(data)"
          >{{ childLabel(data) }}</span
        >
      </span>
    </template>

    <template #formula="{ data }">
      <BaseCopyableField
        v-if="formulaOf(data)"
        class="formula"
        :field="formulaOf(data)"
        :tooltip="data.consensus_formula ? null : ION_TOOLTIP"
      />
    </template>

    <!-- The ion the batch peak is, and the xN pill where the batch reads its
         neutral through N channels: it opens the channels together. -->
    <template #ion="{ data }">
      <span class="ion">{{ data.consensus_ion_formula ?? '' }}</span>
      <button
        v-if="channelGroupOf(data)"
        type="button"
        class="channel-pill"
        data-testid="channel-pill"
        :aria-label="`See the ${channelGroupOf(data).members.length} channels of ${channelGroupOf(data).formula ?? data.consensus_formula} together`"
        v-tooltip.top="channelPillTooltip(data)"
        @click.stop="openLens(channelGroupOf(data))"
      >
        &times;{{ channelGroupOf(data).members.length }}
      </button>
    </template>

    <!-- No number beside this one: a batch peak's consensus tier is a
         weighted vote over its members' tiers, not a threshold on any single
         quantity. The role is the chip on an anchor a source role accounts
         for; what the batch's own rules hold the tier down on is the mark on
         it. -->
    <template #tier="{ data, cap }">
      <BaseTierTag :tier="data.consensus_tier" :role="data.consensus_role" :cap="cap" />
    </template>

    <!-- The batch's own level, else the members', and with Show > Engine
         readings on, the importing engine's own, outlined beside it. -->
    <template #level="{ data }">
      <!-- The chip opens where the level comes from: the members that state
           it, their evidence, and the way to them. -->
      <button
        v-if="data.shownLevel"
        type="button"
        class="badge-button level-button"
        data-testid="level-sources"
        :aria-label="`Level ${data.shownLevel.level}: open the samples it comes from`"
        @click.stop="openLevelSources($event, data)"
      >
        <BaseLevelTag :level="data.shownLevel.level" :tooltip="shownLevelTooltip(data)" />
      </button>
      <BaseLevelTag
        v-if="engineReadings && isLevel(data.engine_level)"
        class="engine-chip"
        data-testid="engine-level-chip"
        :level="data.engine_level"
        :tooltip="engineLevelTooltip(data)"
        outlined
      />
    </template>

    <template #end-columns>
      <Column
        field="n_present"
        sortable
        headerClass="col-samples"
        bodyClass="col-samples"
        style="min-width: 4.5rem"
      >
        <template #header>
          <span v-tooltip.top="HEADER_TOOLTIPS.samples">Samples</span>
        </template>
        <template #body="{ data }">
          <span v-if="!data.parentId && !data.ledgerKind" class="samples" data-testid="samples">{{
            samplesLabel(data)
          }}</span>
        </template>
      </Column>

      <!-- Across the batch: the batch's readings, at most three badges, the
           ones that hold the reading back first, then "+n". -->
      <Column
        field="acrossRank"
        sortable
        headerClass="col-across"
        bodyClass="col-across"
        style="min-width: 6.5rem"
      >
        <template #header>
          <span
            v-tooltip.top="HEADER_TOOLTIPS.across"
            v-help.top="{
              title: 'Across the Batch',
              helpKey: 'assignment-tiers',
              doc: app.ui.help.docUrl('how-it-works/peak-assignment/#across-the-batch')
            }"
            >Across the batch</span
          >
        </template>
        <template #body="{ data }">
          <span v-if="!data.parentId && !data.ledgerKind" class="across" data-testid="across">
            <template v-for="reading in acrossShown(data)" :key="reading.id">
              <!-- Ratio stability opens the pairs behind its grade. -->
              <button
                v-if="reading.id === 'ratio'"
                type="button"
                class="badge-button"
                data-testid="continuity"
                :aria-label="`Ratio stability: open the pairs behind the grade`"
                @click.stop="openContinuity($event, data)"
              >
                <BaseEvidenceBadge
                  :mark="reading.id"
                  :record="reading.record"
                  :data-testid="`mark-${reading.id}`"
                />
              </button>
              <!-- A badge whose reading names other rows - a series, a
                   co-variation pair, a batch check's pair - opens them
                   together. -->
              <button
                v-else-if="badgeGroup(data, reading.id)"
                type="button"
                class="badge-button"
                :data-testid="`lens-${reading.id}`"
                :aria-label="`${markOf(reading.id).label}: see its rows together`"
                @click.stop="openLens(badgeGroup(data, reading.id))"
              >
                <BaseEvidenceBadge
                  :mark="reading.id"
                  :record="reading.record"
                  :data-testid="`mark-${reading.id}`"
                />
              </button>
              <BaseEvidenceBadge
                v-else
                :mark="reading.id"
                :record="reading.record"
                :data-testid="`mark-${reading.id}`"
              />
            </template>
            <!-- "+n" opens every badge of the row with its reading. -->
            <button
              v-if="acrossMore(data).length"
              type="button"
              class="across-more"
              data-testid="across-more"
              aria-haspopup="dialog"
              :aria-expanded="
                String(
                  Boolean(acrossMenu?.visible) && acrossTarget?.batch_peak_id === data.batch_peak_id
                )
              "
              :aria-label="acrossMoreTooltip(data)"
              v-tooltip.top="acrossMoreTooltip(data)"
              @click.stop="openAcross($event, data)"
            >
              +{{ acrossMore(data).length }}
            </button>
          </span>
        </template>
      </Column>
    </template>

    <!-- The row's action: the brightest sample, with this peak in focus. With
         several traces plotted it is not obvious which point of the chart to
         click, and the brightest sample is where the species is best
         measured. -->
    <template #actions="{ data }">
      <button
        type="button"
        class="jump-cell"
        :disabled="jump.pendingId != null || !data.n_present"
        aria-label="Open the brightest sample with this peak in focus"
        v-tooltip.top="
          'Open the brightest sample with this peak in focus (Sample tab). The brightest ' +
          'sample, not where the level comes from: click the level for those samples'
        "
        @click.stop="openBrightest(data)"
      >
        <span
          :class="
            jump.pendingId === data.batch_peak_id
              ? 'pi pi-spin pi-spinner'
              : 'pi ph ph-arrow-square-out'
          "
        />
      </button>
    </template>

    <template #popovers>
      <Popover ref="continuityMenu" aria-label="Ratio stability">
        <BatchPeakContinuityPopover
          v-if="continuityTarget"
          :row="continuityTarget"
          :isotopeLine="Boolean(continuityTarget.parentId)"
        />
      </Popover>

      <Popover ref="acrossMenu" aria-label="Across the batch" @hide="returnAcrossFocus">
        <BatchAcrossPopover
          v-if="acrossTarget"
          :readings="acrossReadings(acrossTarget)"
          @act="actFromAcross"
        />
      </Popover>

      <Popover ref="levelMenu" aria-label="Where the level comes from">
        <LedgerLevelSources
          v-if="levelTarget"
          :claim="claimOf(levelTarget)"
          :batchLevel="batchLevelOf(levelTarget)"
          :memberLevel="levelTarget.member_level ?? null"
          :memberLevels="levelTarget.member_levels ?? null"
          :sources="levelSources.id === levelTarget.batch_peak_id ? levelSources.body : null"
          :loading="levelSources.loading"
          :error="levelSources.id === levelTarget.batch_peak_id ? levelSources.error : null"
          :snapshot="viewingSnapshot"
          :canAssign="assign.canAssign"
          :assignBlocked="assign.blockedReason || null"
          @open-member="openLevelMember"
          @assign="assignFromLevel"
        />
      </Popover>

      <Popover ref="verdictMenu" aria-label="Batch-level verdict">
        <LedgerVerdictPopover
          v-if="verdictTarget"
          scope="batch"
          :row="verdictTarget"
          :record="verdictFor(verdictTarget)"
          :stale="verdictStale(verdictTarget)"
          @done="verdictMenu?.hide()"
        />
      </Popover>
    </template>
  </AssignmentLedger>
</template>

<style scoped>
.instrument-partition {
  display: flex;
  align-items: center;
}

/* A stamped line's substitution, in italics where the stamping predicted it
   rather than the samples' runs finding it. Not dashed: a dashed border means
   not yet known, and a predicted line is a reading. */
.child-label.stamped,
.child-label.stripped {
  padding: 0 0.25rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 3px;
}
.child-label.predicted {
  font-style: italic;
}
/* A row stripped to its family's isotope line. */
.child-label.stripped {
  border-color: var(--p-primary-color);
}
/* A line is its family's species and carries no box: the family's row
   plots it. Nor does a row that stands for no batch peak. */
:deep(tr.ledger-line .p-checkbox),
:deep(tr.ledger-pseudo .p-checkbox) {
  visibility: hidden;
}
.ion {
  font-family: var(--font-mono);
  font-size: 0.82rem;
}
/* The xN pill: how many channels of the batch read the neutral. */
.channel-pill {
  display: inline-flex;
  align-items: center;
  margin-left: 0.35rem;
  padding: 0 0.3rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 999px;
  background: transparent;
  color: var(--p-text-muted-color);
  font: inherit;
  font-size: 0.68rem;
  font-variant-numeric: tabular-nums;
  line-height: 1.4;
  cursor: pointer;
}
.channel-pill:hover {
  color: var(--p-text-color);
  background: var(--p-content-hover-background);
}

.samples {
  font-variant-numeric: tabular-nums;
}

/* Across the batch: the badges in a row, then the count of the rest. */
.across {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}
.badge-button {
  display: inline-flex;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: none;
  cursor: pointer;
}
.badge-button :deep(.evidence-badge) {
  cursor: pointer;
}
.across-more {
  padding: 0.05rem 0.25rem;
  border: 0;
  border-radius: 4px;
  background: none;
  font: inherit;
  font-size: 0.72rem;
  font-variant-numeric: tabular-nums;
  color: var(--p-text-muted-color);
  cursor: pointer;
}
.across-more:hover {
  color: var(--p-text-color);
  background: var(--p-content-hover-background);
}

/* The row's action, the jump: the spinner marks the one jump in flight. */
.jump-cell {
  padding: 0.05rem 0.25rem;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.jump-cell:hover {
  background: var(--p-content-hover-background);
}
.jump-cell:disabled {
  cursor: default;
  opacity: 0.2;
}
</style>
