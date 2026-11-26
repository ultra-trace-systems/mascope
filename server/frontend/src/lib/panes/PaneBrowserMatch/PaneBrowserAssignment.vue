<script setup>
import { ref, reactive, computed, watch, nextTick, onScopeDispose } from 'vue'

import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Column from 'primevue/column'
import Dialog from 'primevue/dialog'
import Message from 'primevue/message'
import Popover from 'primevue/popover'
import ProgressSpinner from 'primevue/progressspinner'
import { FilterMatchMode } from '@primevue/core/api'

import { getApiErrorMessage, isRefusedRequest } from '@/api/utils'
import {
  BaseCopyableField,
  BaseEmptyState,
  BaseLevelTag,
  BaseLoadError,
  BaseTierTag
} from '@/lib/base'
import { PeakAssignConfigForm } from '@/lib/dialogs'
import {
  PARAM_KEYS,
  batchSettingsModel,
  runConfig,
  usePeakAssignParams
} from '@/lib/peakAssignParams'
import { neutralKey } from '@/lib/chem'
import { isIsotopeLine } from '@/lib/isotopeLines'
import { ledgerListing, listingName, targetName, targetOf } from '@/lib/referenceListings'
import { cappingRulesOf, reasonTooltip } from '@/lib/tierReasons'
import {
  ENGINE_UNSTATED,
  ROLE_BUCKETS,
  bucketOf as bucketFor,
  bucketRank,
  engineBucketOf,
  tierName,
  tierRank
} from '@/lib/tiers'
import {
  isLevel,
  isShownLevel,
  levelFilterOptions,
  levelMeta,
  levelRank,
  readerLevel
} from '@/lib/levels'
import { queueReason, queueRows, queueTooltip } from '@/lib/verificationQueue'
import { scrollVirtualRowIntoView } from '@/lib/virtualScroll'
import {
  AssignmentLedger,
  ENTRY_GAP,
  ENTRY_MEMBER,
  GROUP_CHANNELS,
  LEDGER_ROW_HEIGHT,
  LedgerMenuChoice,
  LedgerVerdictPopover,
  LedgerViewToggle,
  groupSections,
  ledgerTierChips,
  lensColumnLabel,
  lensEntries,
  lensTitle,
  localChannelGroup,
  runSearchWindow,
  sampleGroupSets,
  sampleLineLabel,
  useFamilyFold,
  useLedgerFilters,
  useLedgerLensStore,
  useLedgerSort,
  useLedgerView,
  usePendingRowFocus,
  useRowPopover,
  verdictRank
} from '@/lib/ledger'
import { usePeakSearch } from '@/lib/panes/PanePeakAssign/stores/peakSearch.js'
import { useApp } from '@/stores'

import { useAssignmentLauncher } from './stores'
import { useBatchChartSelection } from './stores/batchChartSelection.js'

/**
 * The sample scope of the assignment ledger (lib/ledger/AssignmentLedger.vue):
 * every peak of the focused sample with its committed assignment from the
 * selected run. This pane is the scope's configuration - its rows, its Plot
 * box, its Ion cell (the mechanism, and how many of the sample's rows read the
 * same neutral), the queue's reason, its strip item (the verification queue),
 * its row action (Curate) and its single selection, tied to the focused peak -
 * and owns the Assign sample dialog.
 */
const app = useApp()
const launcher = useAssignmentLauncher()

// Every choice of the filter bar and its Show menu, held outside the pane and
// persisted (lib/ledger/useLedgerView.js), so it survives the switch to the
// batch ledger and back, and a reload.
const view = useLedgerView('sample')
const viewChoice = (key) =>
  computed({
    get: () => view[key],
    set: (value) => (view[key] = value)
  })

const runs = computed(() => app.data.peakAssignment.run)
const assignments = computed(() => app.data.peakAssignment.peak)
const queue = computed(() => app.data.peakAssignment.queue)

// Whether this run carries a tier of its producing engine's own. Only an
// imported run does, and a sample read from the batch ledger on the M0 row an
// import's verdict is about - an in-app row's engine tier IS its `tier` - so the column
// is absent from every in-app ledger rather than sitting there empty. Read off
// the loaded rows rather than the run's engine, because "peaky published this"
// and "peaky stated a tier on these rows" are different claims: an engine may
// publish without tiering anything.
const hasEngineTiers = computed(() => assignments.value.list.some((row) => row.engine_tier != null))

// The engine-tier view: the tier strip counts, and its chips filter, by the
// producing engine's own tier rather than this server's, so an imported run can
// be read in its engine's words. Off by default, offered only where the run
// carries engine tiers, and a reading aid alone: the tier column, the sort and
// everything rolled up across the batch stay on this server's tier.
const engineView = viewChoice('engineView')
const readsEngineTiers = computed(() => engineView.value && hasEngineTiers.value)

// The strip's counts in the engine's words: the store's histogram's population
// (an isotopologue line counts with its compound, not on its own), bucketed by
// `engineBucketOf`.
function engineTierHistogram(records) {
  const counts = { [ENGINE_UNSTATED]: 0 }
  for (const record of records ?? []) {
    if (record.role === 'iso_child') continue
    const bucket = engineBucketOf(record.engine_tier, record.tier, record.role)
    counts[bucket] = (counts[bucket] ?? 0) + 1
  }
  return counts
}
const tierChips = computed(() =>
  ledgerTierChips(
    readsEngineTiers.value
      ? engineTierHistogram(assignments.value.list)
      : assignments.value.tierCounts,
    { engineView: readsEngineTiers.value, roles: view.roles, active: activeTiers.value }
  )
)

// What a reader may state of each row, in the scale peaky and this server share
// (lib/levels.js), shown in a column of its own beside the tier and never in
// its place. Offered only where some row of the run carries one, as the engine
// tier is: a run from before the level, an imported run and a ledger the batch
// serves carry none, and a column of empty cells would read as rows judged and
// found wanting.
//
// The level a row shows is the live claim's where its verdict states one, else
// the engine's (readerLevel): its own verdict's, else the batch-level verdict
// reaching it, which the server serves with no level to a sample a level-1
// claim's window leaves out. So a run with no engine levels still offers the
// column where a claim states one. Defined below the verdicts it reads.
const hasLevels = computed(() =>
  assignments.value.list.some((row) => isShownLevel(shownLevelOf(row)?.level))
)

// The level the producing engine stated, beside this server's as its tier is
// beside the tier: an imported run's, and a row derived from the batch ledger
// where a batch import stated one. Read off the loaded rows, as the engine
// tier is.
const hasEngineLevels = computed(() =>
  assignments.value.list.some((row) => isLevel(row.engine_level))
)

// Show > Engine readings: the engine's own tier and level as a second,
// outlined chip in the Tier and Level cells, where a row carries them, as the
// batch ledger shows them (owner's decision of 2026-11-22: the chip, not
// columns of their own). Offered where the run carries either.
const hasEngineReadings = computed(() => hasEngineTiers.value || hasEngineLevels.value)
const engineReadings = computed(() => engineView.value && hasEngineReadings.value)
// The Level column, where any row carries a level its cell draws: its own or a
// claim's, or with the engine readings on, the engine's. The level filter stays
// on the level the column's first chip shows (`hasLevels`).
const hasLevelColumn = computed(
  () => hasLevels.value || (engineReadings.value && hasEngineLevels.value)
)

// Map ionization_mechanism_id -> readable notation (e.g. "[M+H]+", "[M+Br]-"), for
// the ledger's ionization column. The assignment carries only the id.
const mechById = computed(() => {
  const map = new Map()
  for (const mech of app.data.ionization.mechanism.list) {
    map.set(mech.ionization_mechanism_id, mech.ionization_mechanism)
  }
  return map
})

// --- Verdicts ------------------------------------------------------------------

// Current verification verdict for a ledger row (by stable identity). A
// formula-less row is a placeholder for a peak nothing explained: there is no
// assignment to have judged, so it never carries a verdict - not in the badge
// column, and not in the verdict filter, which would otherwise sort it under a
// verdict whose badge the row does not show. (The stable identity
// `sample_peak_id|assigned_formula|ionization_mechanism_id` is degenerate
// without a formula anyway, so a verdict left by an earlier run could match the
// wrong peak.)
//
// Both the guard and the lookup resolve through the family M0: an unfolded
// isotopologue is the same compound as the parent above it, so it shows the same
// badge rather than a blank cell that reads as "not yet judged". The filter
// runs over parents only, so a family is one unit there already - keeping the
// two in step is the point.
const verdictFor = (row) => {
  const m0 = assignments.value.m0Of(row)
  return m0?.assigned_formula ? app.data.peakAssignment.verification.forAssignment(m0) : null
}

// The batch-level verdict that reaches a row with no verdict of its own: the
// family M0's peak folded into a judged batch peak, and the judgment is about the
// M0's own formula and mechanism - a dissenting row gets no overlay from a verdict
// about another formula. A per-sample verdict always wins; where both exist and
// disagree, the per-sample badge says so in its tooltip.
const overlayFor = (row) => {
  const m0 = assignments.value.m0Of(row)
  return m0?.assigned_formula ? app.data.peakAssignment.anchorContext.overlayFor(m0) : null
}
const conflictFor = (row) => {
  const own = verdictFor(row)
  const overlay = own ? overlayFor(row) : null
  return overlay && overlay.verdict !== own.verdict ? overlay : null
}
// What the row visibly carries, its own verdict first. The filter and the
// verdict column's sort run on this, so "Unverified" never lists a row that
// shows a badge.
const effectiveVerdictFor = (row) => verdictFor(row) ?? overlayFor(row)

// The level the row shows, and whose it is. An isotopologue reads its M0's
// verdict, as its badge does, and carries its M0's engine level already.
const shownLevelOf = (row) =>
  readerLevel({
    verdict: effectiveVerdictFor(row),
    level: row.level,
    evidence: row.level_evidence,
    lift: row.level_lift
  })
const levelOptions = computed(() =>
  levelFilterOptions(decoratedRows.value.map((row) => row.shownLevel?.level))
)

// The verdict filter's choices (single-select), in the Show menu.
const VERDICT_FILTERS = [
  { value: 'all', label: 'All verdicts' },
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'unsure', label: 'Unsure' },
  { value: 'unverified', label: 'Unverified' }
]

const verdictTooltip = (row) => {
  if (verdictFor(row)) return 'Verification verdict - click to re-judge'
  if (overlayFor(row)) return 'Batch-level verdict reaching this sample - click to judge it here'
  return 'Record a verdict'
}
const verdictCell = (row) => {
  const record = verdictFor(row)
  return {
    record,
    overlay: record ? null : overlayFor(row),
    conflict: conflictFor(row),
    label: record ? 'Verification verdict' : 'Record a verdict',
    tooltip: verdictTooltip(row)
  }
}

// --- The verification queue ---------------------------------------------------

// The assignments worth a verdict next, as the server ranks them
// (lib/verificationQueue.js): the rows near a tier edge where the instrument
// holds few labels, random spot checks between them, and first the verdicts a
// later run read differently. The chip at the end of the tier strip counts them
// and narrows the table to them, in the queue's order: entering clears a column
// sort, so the order shown is the one to ask in. The verdict filter does not
// apply while it is on - a stale row carries the verdict it is queued to have
// judged again.
//
// Guardrail 5 of the loop: the judgment is of the data, not of a number. The
// ledger shows no probability anywhere, and in the queue the tier chip's hover
// drops the evidence as well, the calibrated number's input.
const queueMode = viewChoice('queue')
const queueCount = computed(() => queue.value?.count ?? 0)

// --- Launch a run -----------------------------------------------------------

// The button that opens this dialog is a row up, in the switch bar, so the flag
// is shared through a store while everything the launch actually does stays
// here - next to the ledger a refusal is about. Cleared when this pane goes
// away, because the dialog goes with it: a stale `true` would reopen the dialog
// on the next mount without the watcher below having run.
const configVisible = computed({
  get: () => launcher.configVisible,
  set: (value) => (launcher.configVisible = value)
})
onScopeDispose(() => {
  launcher.configVisible = false
})
const submitting = ref(false)
// The run configuration opens on the batch's settings (its saved
// `assignment_settings` over the server defaults,
// docs/dev/batch_assignment_one_action.md 3.4), so a sample is assigned as its
// batch is unless the dialog is changed. A change is this one run's: it goes
// in the run's request, which the run records, and never back to the batch.
// What the batch saved of a field the form does not show (its tier bands, its
// stage B confidence, ...) goes in the request as saved. Seeded again on every
// open, so last time's override is not carried.
const assignParams = usePeakAssignParams()
const runModel = reactive(Object.fromEntries(PARAM_KEYS.map((key) => [key, null])))
let runSaved = null
const configForm = ref(null)
// Why Assign did nothing yet, said in the dialog that stays open for it.
const formError = ref(null)

function seedRun() {
  runSaved = app.data.batch.focused?.assignment_settings ?? null
  Object.assign(runModel, batchSettingsModel(runSaved, assignParams.defaults))
}

// Why the last launch produced no run. The endpoint decides synchronously, so a
// sample already being assigned or one that cannot usefully be assigned comes
// back as a refusal with a reason - and a reason belongs next to the button that
// earned it, not in a toast that has scrolled away by the time the user reads
// the empty run list.
const launchError = ref(null)
const launchRefused = ref(false)

// Whatever the previous sample was refused for says nothing about this one.
watch(
  () => app.data.sample.focusedId,
  () => (launchError.value = null)
)

// Every open starts on the batch's settings and with no stale refusal. Also
// scope help mode to the dialog's cards while it is open.
watch(configVisible, (open) => {
  if (open) {
    launchError.value = null
    formError.value = null
    seedRun()
    // Seeded again once the defaults land, if the dialog opened before.
    if (!assignParams.loaded) {
      assignParams.ensureLoaded().then(() => configVisible.value && seedRun())
    }
  }
  app.ui.help.set(open ? 'dialog_peak_assign' : null)
})

async function launch() {
  const sampleItemId = app.data.sample.focusedId
  if (!sampleItemId) return
  // A range still being typed is committed first; one that does not parse
  // keeps the dialog open, saying why.
  if (configForm.value && !configForm.value.commit()) {
    formError.value = 'The formula range does not parse. Fix it or clear it to assign.'
    return
  }
  formError.value = null
  submitting.value = true
  try {
    await runs.value.assign(sampleItemId, runConfig(runModel, {}, runSaved))
    launchError.value = null
  } catch (error) {
    // A refusal is an answer, not a crash: the request was understood and
    // declined. Either way the dialog has nothing left to do - closing it puts
    // the reason in view rather than behind a modal.
    launchRefused.value = isRefusedRequest(error)
    launchError.value = getApiErrorMessage(error, 'Could not start the assignment run.')
  } finally {
    submitting.value = false
    configVisible.value = false
  }
}

// --- Focus a peak from the ledger -------------------------------------------

// Clicking an assignment focuses the matching peak (join by peak_id ===
// sample_peak_id) and brings the Sample tab (spectrum + inspector) forward.
// The ledger's rows can show before the sample's peak list does: a click then
// waits as pending and is carried out once the list lands for the same sample
// (lib/ledger/usePendingRowFocus.js), rather than doing nothing (#3588).
const pendingFocus = usePendingRowFocus({
  loading: () => app.data.peak.pending,
  failed: () => Boolean(app.data.peak.error),
  sampleId: () => app.data.sample.focusedId,
  find: (peakId) => app.data.peak.list.find((p) => String(p.peak_id) === String(peakId)) ?? null,
  focus: (peak) => {
    app.data.peak.focus(peak)
    app.ui.tab.active = 'sample'
  }
})
function focusPeak(assignment, then = null) {
  return pendingFocus.request(assignment.sample_peak_id, then)
}

// The row's action: curate it, in the formula search for its peak, where a
// person picks another formula or confirms this one. The search opens beside
// the inspector on the Sample tab, which the focus brings forward.
const peakSearch = usePeakSearch()
function curate(row) {
  focusPeak(row, () => (peakSearch.open = true))
}

// --- Rows whose peak the sample no longer has --------------------------------

// A run keeps the peak ids it read. When the sample's peaks are found again -
// re-ingested, re-detected, restored - the new peaks carry new ids, and the
// runs from before stay in the run selector with rows that name peaks the
// sample no longer has. Such a row still shows its m/z and intensity, which
// the run stored, but there is no peak for a click to focus, and a click that
// did nothing at all read as a broken ledger (#2025). The rows are dimmed and
// the ledger says why above them. Read only once the sample's peaks are in:
// while they load, or for a sample with none listed, nothing is known yet. Nor
// when they failed to load: the store keeps the previous sample's list then,
// and every row would read as stale against it.
const livePeakIds = computed(() => {
  const list = app.data.peak.list
  if (app.data.peak.pending || app.data.peak.error || !list?.length) return null
  return new Set(list.map((peak) => String(peak.peak_id)))
})
const isStale = (row) =>
  livePeakIds.value != null && !livePeakIds.value.has(String(row.sample_peak_id))
const staleCount = computed(() =>
  livePeakIds.value ? assignments.value.list.filter(isStale).length : 0
)
const staleNotice = computed(() => {
  const n = staleCount.value
  if (!n) return ''
  const which =
    n === assignments.value.list.length
      ? "Every row of this run names a peak the sample's peak list no longer has"
      : `${n} ${n === 1 ? 'row' : 'rows'} of this run name a peak the sample's peak list no longer has`
  return (
    `${which}: the peaks were found again after the run, so those rows cannot be opened. ` +
    'Run the assignment again to read the current peaks.'
  )
})
const rowClass = (row) => (!row.ledgerKind && isStale(row) ? 'stale-peak' : null)

// --- Filters ------------------------------------------------------------------

// The roles that account for a peak without a formula (a reagent's ion, an
// artifact's ringing) are buckets of their own after the tiers, filtered,
// counted and sorted apart from the peaks nothing explained (@/lib/tiers). The
// chips filter on the bucket of the view the strip is in; the sort stays on
// this server's tier in both.
const bucketOf = (row) =>
  readsEngineTiers.value
    ? engineBucketOf(row.engine_tier, row.tier, row.role)
    : bucketFor(row.tier, row.role)

// What the search box reads off a row: the formula it shows, the names a list
// and a target give it, and its m/z and its isotope lines', so a line's m/z
// finds the family it folds into. An m/z matches within the run's own mass
// gate (lib/ledger/search.js).
const searchTexts = (row) => {
  const listing = ledgerListing(row.reference_listing)
  const target = targetOf(row.reference_listing, listing)
  return [formulaOf(row), listing ? listingName(listing) : null, target ? targetName(target) : null]
}
const searchMzs = (row) => [
  row.sample_peak_mz,
  ...assignments.value.childrenOf(row.peak_assignment_id).map((line) => line.sample_peak_mz)
]
const searchWindow = computed(() => runSearchWindow(runs.value.focused))

const { activeTiers, toggleTier, filters, verdictFilter, searchQuery, passes } = useLedgerFilters({
  bucketOf,
  state: view,
  menus: { level: FilterMatchMode.EQUALS },
  // The menu filters on the level the column draws, a claim's included.
  valueOf: { level: (row) => row.shownLevel?.level ?? null },
  menuShown: { level: hasLevels },
  view: readsEngineTiers,
  verdictOf: effectiveVerdictFor,
  search: { texts: searchTexts, mzs: searchMzs },
  tolerancePpm: searchWindow
})
const searchTooltip = computed(() => {
  const { ppm, centrePpm } = searchWindow.value
  return (
    'A formula, a listed name, or an m/z: an m/z matches within ' +
    `${ppm.toFixed(1)} ppm` +
    (centrePpm ? ` of the run's fitted centre (${centrePpm.toFixed(1)} ppm)` : '') +
    (runs.value.focused?.sigma_ppm ? ", the run's own mass gate" : '')
  )
})

// Fold isotopologues by default: one row per assigned formula (M0)
// plus unassigned/reagent peaks, which keeps this a flat, fixed-height list
// compatible with virtual scrolling.
const showIsotopologues = viewChoice('isotopologues')
// One family at a time, from the caret on its row, against Show's choice.
const families = useFamilyFold(showIsotopologues)

// --- One neutral, several channels -------------------------------------------------

// A run that searches several ionization channels can commit the same neutral
// through more than one of them - [M+H]+ and [M+NH4]+, [M-H]- and [M+Br]- - and
// the ledger lists each as a row of its own, at its own m/z. Only an analyte's
// formula names a neutral; a source ion or a peak nothing explained names none.
const groupKeyOf = (row) =>
  row.assigned_formula && !ROLE_BUCKETS.includes(row.role) ? neutralKey(row.assigned_formula) : ''

// The Ion cell's pill: how many of the sample's own rows read the row's
// neutral, through how many channels (section 3.2: `xN`, counted on the
// sample's rows, which needs no batch). Counted over the run's rows, not the
// rows the filters leave, so the pill says the same whatever the reader has
// narrowed the ledger to. An isotope line is its M0's reading again and does
// not count.
const channelsByNeutral = computed(() => {
  const byKey = new Map()
  for (const row of assignments.value.list) {
    if (isIsotopeLine(row)) continue
    const key = groupKeyOf(row)
    if (!key) continue
    if (byKey.has(key)) byKey.get(key).push(row)
    else byKey.set(key, [row])
  }
  return byKey
})
const channelsOf = (row) => {
  const key = groupKeyOf(row)
  return key ? (channelsByNeutral.value.get(key) ?? []) : []
}
const channelCount = (row) => channelsOf(row).length
// The pill's hover: the channels the neutral is read through, and what
// clicking does.
const channelTooltip = (row) => {
  const mechs = [
    ...new Set(
      channelsOf(row).map(
        (other) => mechById.value.get(other.ionization_mechanism_id) ?? 'another channel'
      )
    )
  ]
  return (
    `The same neutral is read through ${channelCount(row)} channels of this sample: ` +
    `${mechs.join(', ')}. Click to see the channels together`
  )
}

// --- Group by ----------------------------------------------------------------------

// Show > Group by: none, or compound - one group line per neutral two or more
// of the sample's rows read, over those rows, and the rest under an Ungrouped
// line at the end. Grouping by series needs every row's groups at once, which
// the sample's list does not carry: it is the batch scope's.
const GROUP_BY = [
  { value: 'none', label: 'None' },
  {
    value: 'compound',
    label: 'Compound',
    tooltip: "One group line per neutral the sample's rows read through several channels"
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

// --- The lens ----------------------------------------------------------------------

// The ledger narrowed to the rows a reading spans (section 3.5). The sample's
// list carries no groups, so a click on the xN pill reads that row's detail
// for the batch's groups (`batch_groups`) and opens its channels from them:
// every member, the sample's own row where it holds the member and a greyed
// line where it does not. Where the batch serves none - it has no batch peaks
// yet - the lens falls back to the sample's own rows, channels only. The card's
// Show in the ledger opens one here too. Shown only while the sample it was
// opened on is the one listed.
const lenses = useLedgerLensStore()
const lensState = computed(() => {
  const state = lenses.lenses.sample
  return state && state.sampleId === app.data.sample.focusedId ? state : null
})
const lensGroup = computed(() => lensState.value?.group ?? null)
const closeLens = () => lenses.close('sample')

// Each click on a pill counts, so a read that lands after a newer click opens
// nothing: the reader's last click wins, however slow the read behind an
// earlier one.
let lensClicks = 0
async function openChannelLens(row) {
  const sampleId = app.data.sample.focusedId
  const click = ++lensClicks
  const lensBefore = lenses.lenses.sample
  // A failed read has been reported by the http layer; the lens then stands
  // on the sample's own rows.
  const detail = await assignments.value.loadDetail(row).catch(() => null)
  if (app.data.sample.focusedId !== sampleId) return
  // A newer click, or a lens opened or closed since - Escape, ×, the card's
  // Show in the ledger - is what the reader asked for last.
  if (click !== lensClicks || lenses.lenses.sample !== lensBefore) return
  // The detail serves the groups the row's batch peak is in, and a batch peak
  // is in one channel group at most: its neutral's.
  const served = (detail?.batch_groups ?? []).find((group) => group?.kind === GROUP_CHANNELS)
  const group =
    served ??
    localChannelGroup(channelsOf(row), {
      mechanismOf: (other) => mechById.value.get(other.ionization_mechanism_id) ?? null
    })
  if (group) lenses.open('sample', group, { sampleId })
}

// --- Sorting ----------------------------------------------------------------

// What the formula column shows: the analyte, or on a row with none, the ion
// the peak is - a source ion's, written with its charge - since the ion is known
// exactly and the role chip beside it says whose it is. A row with neither is
// blank.
const formulaOf = (row) => row.assigned_formula || row.ion_formula || null
const ION_TOOLTIP = 'Ion formula: the peak names an ion, and no compound of the sample'

// The sort is held per scope across the switch between the two ledgers, and a
// reload (lib/ledger/useLedgerSort.js). The formula column sorts on what it
// shows; the listing on its name.
const {
  field: sortField,
  order: sortOrder,
  sort
} = useLedgerSort('sample', { assigned_formula: (row) => formulaOf(row) })

// The ledger's resting order: most confident first, best fit first within a
// tier. Also the tie-breaker under every other column, and what an unsorted
// table (third click on a sorted header) falls back to.
const byConfidence = (a, b) => a.tierRank - b.tierRank || (b.fit_score ?? -1) - (a.fit_score ?? -1)
// The verification queue's resting order: the server's.
const byQueue = (a, b) => a.queueRank - b.queueRank
// Entering the queue shows it in its own order, not under last sort's column.
watch(queueMode, (on) => {
  if (on) sortField.value = null
})

// The isotope lines of a row, as rows under it: an analyte's isotopologues, a
// source ion's heavier lines (isIsotopeLine), ordered by m/z among themselves.
function isotopologuesOf(parent) {
  return assignments.value
    .childrenOf(parent.peak_assignment_id)
    .slice()
    .sort((a, b) => (a.sample_peak_mz ?? 0) - (b.sample_peak_mz ?? 0))
    .map((child) => ({
      ...child,
      // Only the parents are sorted - children are spliced in under their
      // parent whatever the sort - so a family sorts as one block.
      tierRank: parent.tierRank,
      // A line is its M0's formula measured again: it shows its label, its m/z
      // and its intensity, and repeats nothing of its M0's row.
      listing: null,
      target: null,
      mech: mechById.value.get(child.ionization_mechanism_id) ?? null,
      shownLevel: shownLevelOf(child),
      isChild: true
    }))
}

// Every row of the run that stands on its own (M0 + unassigned/reagent),
// decorated with the ranks the columns sort on. The filters pick from these;
// the lens and Group by's sets read all of them.
const decoratedRows = computed(() =>
  assignments.value.list
    .filter((row) => !isIsotopeLine(row))
    .map((row) => {
      const listing = ledgerListing(row.reference_listing)
      const shownLevel = shownLevelOf(row)
      return {
        ...row,
        tierRank: bucketRank(row.tier, row.role),
        // Null where the producing engine stated no tier, so those rows sort
        // last in both directions: "this engine said nothing" is not a
        // position on the scale.
        engineTierRank: row.engine_tier != null ? tierRank(row.engine_tier) : null,
        // Null rather than a rank where the row shows no level, likewise.
        shownLevel,
        levelRank: shownLevel ? levelRank(shownLevel.level) : null,
        verdictRank: verdictRank(effectiveVerdictFor(row)),
        listing,
        target: targetOf(row.reference_listing, listing),
        mech: mechById.value.get(row.ionization_mechanism_id) ?? null,
        isChild: false
      }
    })
)
const rowByPeak = computed(
  () => new Map(decoratedRows.value.map((row) => [String(row.sample_peak_id), row]))
)

// Table rows. Parents are filtered by the active chips and the search, then
// ordered by the sorted column with confidence breaking ties - or, in the
// verification queue, the queue's rows in the queue's order - and with Group
// by compound, set under their group lines. When unfolded, each parent's
// isotope lines are inserted right after it: a family is one block wherever
// its parent lands, which is the only arrangement in which the indented child
// rows can be read at all. The lens lists its group instead, whatever the
// filters keep.
const rows = computed(() => {
  if (lensGroup.value) return lensRows.value
  const ledger = decoratedRows.value
  const parents = (queueMode.value ? queueRows(ledger, queue.value?.list) : ledger).filter((row) =>
    passes(row, { verdicts: !queueMode.value })
  )
  sort(parents, queueMode.value ? byQueue : byConfidence)

  const result = []
  const withFamily = (row) => {
    result.push(row)
    if (families.isOpen(row.peak_assignment_id)) result.push(...isotopologuesOf(row))
  }
  const idOf = (row) => String(row.sample_peak_id)
  const sets = view.groupBy === 'compound' ? sampleGroupSets(ledger, groupKeyOf, idOf) : []
  if (!sets.length) {
    parents.forEach(withFamily)
    return result
  }
  for (const section of groupSections(parents, sets, idOf)) {
    const set = sets.find((entry) => entry.key === section.key)
    const best = (set?.ids ?? [])
      .map((id) => rowByPeak.value.get(id))
      .filter(Boolean)
      .sort((a, b) => a.tierRank - b.tierRank)[0]
    const isCollapsed = collapsed.has(section.key)
    result.push({
      sample_peak_id: `group:${section.key}`,
      peak_assignment_id: `group:${section.key}`,
      ledgerKind: 'group',
      groupKey: section.key,
      groupName: section.name,
      groupCount: `${section.size} row${section.size === 1 ? '' : 's'}`,
      groupTier: section.ungrouped ? null : (best?.tier ?? null),
      groupRole: section.ungrouped ? null : (best?.role ?? null),
      collapsed: isCollapsed
    })
    if (!isCollapsed) section.rows.forEach(withFamily)
  }
  return result
})

// Under the lens: the group's members in its order, each labelled by its step
// or its channel - the sample's own row where it holds the member, a greyed
// "not in this sample" line where it does not, and a series step with no batch
// peak as a greyed gap, labelled apart. A sort picked under the lens orders the
// sample's rows by its column, the greyed lines after them.
const memberRow = (member) =>
  member.sample_peak_id != null
    ? (rowByPeak.value.get(String(member.sample_peak_id)) ?? null)
    : null
const lensRows = computed(() => {
  const group = lensGroup.value
  if (!group) return []
  const entries = lensEntries(group, memberRow)
  const members = entries
    .filter((entry) => entry.entry === ENTRY_MEMBER)
    .map((entry) => ({ ...entry.row, lensLabel: entry.label }))
  const pseudo = (entry) => {
    const id = `lens:${entry.key}`
    return entry.entry === ENTRY_GAP
      ? {
          sample_peak_id: id,
          peak_assignment_id: id,
          ledgerKind: 'gap',
          lensLabel: entry.label,
          pseudoFormula: entry.formula,
          pseudoNote: 'no batch peak'
        }
      : {
          sample_peak_id: id,
          peak_assignment_id: id,
          ledgerKind: 'absent',
          lensLabel: entry.label,
          pseudoMz: entry.member.mz,
          pseudoFormula: entry.member.formula,
          pseudoIon: entry.member.mechanism ?? entry.member.ion_formula,
          pseudoNote: 'not in this sample'
        }
  }
  const result = []
  const withFamily = (row) => {
    result.push(row)
    if (families.isOpen(row.peak_assignment_id)) result.push(...isotopologuesOf(row))
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
  if (lensGroup.value) lenses.sortedUnder('sample')
})
// The lens bar's line and its label column. No Plot the group here: the
// batch chart is the batch scope's, and the card offers Plot them.
const lens = computed(() => {
  const group = lensGroup.value
  if (!group) return null
  return {
    title: lensTitle(group, lensEntries(group, memberRow), { scope: 'sample' }),
    column: lensColumnLabel(group),
    plot: false
  }
})

// An unfolded line's label: its substitution wherever the row names one,
// counted from the family's M0 (lib/ledger/rows.js).
const childLabel = (row) => sampleLineLabel(row, assignments.value.m0Of(row))

// Two-way selection tied to the focused peak: clicking a row focuses its peak,
// and focusing a peak elsewhere (spectrum click, inspector) highlights its row.
// When a folded-out isotopologue is focused, highlight its M0 row.
const selectedRow = computed({
  get: () => {
    // A row clicked while the peaks load holds the selection until they land.
    const waiting = pendingFocus.pending.value
    if (waiting) {
      const row = rows.value.find((r) => String(r.sample_peak_id) === String(waiting.peakId))
      if (row) return row
    }
    const focused = app.data.peak.focused
    if (!focused) return null
    // Prefer the exact row for the focused peak (present for M0/standalone rows,
    // and for isotopologue children when unfolded).
    const exact = rows.value.find((r) => String(r.sample_peak_id) === String(focused.peak_id))
    if (exact) return exact
    // Folded: a focused isotopologue child maps to its M0 row.
    const assignment = assignments.value.forPeak(focused.peak_id)
    const ownerId = isIsotopeLine(assignment)
      ? assignment.owner_peak_assignment_id
      : (assignment?.peak_assignment_id ?? null)
    if (ownerId == null) return null
    return rows.value.find((r) => r.peak_assignment_id === ownerId) ?? null
  },
  set: (row) => {
    // A group line, a gap or a member the sample lacks names no peak of the
    // sample, so focusPeak finds none to focus.
    // Clicking the selected row again de-selects it, and PrimeVue says so by
    // emitting null. Selection lives in the peak store, so unfocusing there
    // clears the spectrum highlight, the inspector and the timeseries too.
    if (row) focusPeak(row)
    else {
      pendingFocus.clear()
      app.data.peak.unfocus()
    }
  }
})

// --- Verdict from the row ----------------------------------------------------
// An unverified row is judged where it is read, in the same form the inspector
// carries (lib/ledger/LedgerVerdictPopover.vue).
const {
  menu: verdictMenu,
  target: verdictTarget,
  open: openVerdict
} = useRowPopover(
  (id) => rows.value.find((row) => row.peak_assignment_id === id),
  (row) => row.peak_assignment_id
)

// --- The batch chart from the row ------------------------------------------
// The batch chart draws the Batch peaks ledger's selection; a row's species
// goes into it (or out of it) from here, without leaving the sample.
const chart = useBatchChartSelection()

// --- Keep the focused row in view --------------------------------------------
// A peak focused elsewhere - the batch chart, the batch ledger's jump, the
// inspector's isotopologue table - selects its row here, and with a virtual
// scroller a selected row is in view only if it is scrolled to. Rerun when the
// rows arrive as well: after a sample switch the focus lands before the
// sample's ledger has loaded, and the row exists only once it has.
const ledgerRef = ref()
function scrollToFocusedRow() {
  const row = selectedRow.value
  if (!row) return
  const index = rows.value.indexOf(row)
  nextTick(() =>
    scrollVirtualRowIntoView(ledgerRef.value?.table, index, { itemSize: LEDGER_ROW_HEIGHT })
  )
}
watch([() => selectedRow.value?.sample_peak_id, () => rows.value.length], scrollToFocusedRow, {
  immediate: true
})

// Says which of the two tiers this chip is, and whether it agrees. Without it
// two chips on one row read as one claim rendered twice. Both tiers are named
// in words, not as the API's keys.
const engineTierTooltip = (row) =>
  row.engine_tier === row.tier
    ? `The producing engine's own tier: ${tierName(row.engine_tier)} (agrees with this server's)`
    : `The producing engine's own tier: ${tierName(row.engine_tier)}, where this server's ` +
      `banding of the evidence says ${tierName(row.tier)}`

// Says whose level the outlined chip is, and how it stands against this
// server's own level for the row, as the inspector's chip does.
const engineLevelTooltip = (row) => {
  const own = row.level
  const against = !isLevel(own)
    ? 'Mascope states no level of its own for this row'
    : own === row.engine_level
      ? "It agrees with Mascope's level"
      : `Mascope's level for this row is ${own}`
  return [
    `The producing engine's own level: ${row.engine_level}`,
    levelMeta(row.engine_level)?.description,
    against
  ]
    .filter(Boolean)
    .join('\n')
}

// Isotopologues folded under a formula's M0: the count beside the family's
// caret, and whether they are unfolded under it.
const isoCount = (row) => assignments.value.childrenOf(row.peak_assignment_id).length
const familyOpen = (row) => families.isOpen(row.peak_assignment_id)
const toggleFamily = (row) => families.toggle(row.peak_assignment_id)

// The tier chip's cap mark: the reasons that take the row's top tier, as the
// run recorded them. Named on hover with what each did at the row's tier; the
// card says why at length.
const tierCap = (row) => {
  const caps = cappingRulesOf(row.tier_reason_rules)
  if (!caps.length) return null
  const did = reasonTooltip({ caps: true }, row.tier)
  return {
    label: `Held down by: ${caps.map((cap) => cap.label).join(', ')}`,
    tooltip: `${did}: ${caps.map((cap) => cap.label).join(', ')}. Open the row for why.`
  }
}

// The bars of the intensity column are drawn over every peak of the run.
const intensities = computed(() => assignments.value.list.map((row) => row.sample_peak_intensity))

// The ledger's count, in its header line: the shell's breadcrumb names the
// batch and the sample.
const count = computed(() => {
  const n = assignments.value.list.length
  return `${n.toLocaleString('en-US')} peak${n === 1 ? '' : 's'}`
})

const FIELDS = {
  mz: 'sample_peak_mz',
  intensity: 'sample_peak_intensity',
  formula: 'assigned_formula',
  ion: 'mech',
  level: 'level',
  levelSort: 'levelRank'
}
const HEADERS = {
  formula:
    'The formula, with what a reference list or a target names it under it: the first name ' +
    "the run matched from a list, that list, and the list's tags. A formula match names " +
    'candidate compounds; it is not an identification.',
  ion: 'Ionization mechanism (adduct): the channel the neutral was read through',
  level:
    'Identification level: what a reader may state of the row, from 5 (a measured mass) ' +
    'to 1 (a confirmed structure). Shown beside the tier; no tier moves on it',
  verdict: 'Verification verdict'
}
</script>

<template>
  <AssignmentLedger
    ref="ledgerRef"
    scope="sample"
    label="Assignments"
    icon="pi ph ph-list-magnifying-glass"
    :count="app.data.sample.focused && runs.list.length ? count : ''"
    :panelPt="
      app.ui.help.right(
        `
        <h1>Assignments</h1>
        <p>
        Every peak in the selected sample with its committed assignment from the
        selected run: formula, ionization and confidence tier. The tiering is
        still being developed, and the tier column says so. Where the run
        states them, the <b>Level</b> column gives each row's identification
        level: what may be stated of it, from a measured mass (5) to a
        confirmed structure (1).
        </p>
        <p>
        Click a row to focus the peak in the spectrum and inspector. Search
        for a formula, a listed name or an m/z, use the tier chips to filter
        by confidence and <b>Show</b> for isotopologue lines, roles, the
        verdict filter and grouping, and <b>Assign sample</b> in the bar above
        to launch a new run.
        </p>
        <p>
        The <b>to verify</b> chip shows the assignments worth a verdict next:
        those near a tier edge, random spot checks, and verdicts a later run
        read differently.
        </p>`,
        { doc: app.ui.help.docUrl('how-it-works/peak-assignment/') }
      )
    "
    :pending="assignments.pending"
    :error="assignments.error"
    errorFallback="Could not load this run."
    :onRetry="() => assignments.load('retry')"
    emptyTitle="No peaks match these filters"
    :rows="rows"
    :intensities="intensities"
    mzAction="Open this peak in the inspector"
    v-model:sortField="sortField"
    v-model:sortOrder="sortOrder"
    :fields="FIELDS"
    :levelOptions="levelOptions"
    :headers="HEADERS"
    intensityLabel="Intensity"
    v-model:search="searchQuery"
    :searchTooltip="searchTooltip"
    :chips="tierChips"
    :activeTiers="activeTiers"
    :engineView="readsEngineTiers"
    engineViewTooltip="The chips count and filter by the producing engine's own tier. The tier column and the sort stay on this server's."
    viewMenuTooltip="Show: isotopologue lines, roles, engine readings, the verdict filter, Group by"
    :viewMenuPt="
      app.ui.help.top(
        `
        <h1>Show</h1>
        <p>
        What the ledger's rows show, rather than what it is reading.
        <b>Isotopologue lines</b> unfolds each compound's isotopologue peaks -
        folded under the caret and line count beside the formula by default -
        as indented rows under their main peak (M0), each named by its
        substitution. <b>Roles</b>
        shows the reagent's and the artifacts' peaks; off, the ledger lists
        the sample's compounds and the peaks nothing explained.
        </p>
        <p>
        <b>Verdict</b> narrows the table to one verification verdict.
        <b>Group by</b> compound sets the rows of a neutral read through
        several ionization channels under one group line, which folds them;
        the rest sit under <b>Ungrouped</b>. The &times;N pill beside an
        ionization shows a neutral's channels together, as the batch lists
        them, until &times; or Escape closes the lens.
        </p>
        <p>
        On a run imported from another engine that states tiers or levels
        of its own, <b>Engine tier and level</b> shows them as a second,
        outlined chip in the Tier and Level cells, and makes the tier chips
        count and filter by that engine's tier, with <b>not stated</b> for
        the formulas it gave none. The tier column and the sort stay on
        Mascope's tier.
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
        ledger: they hold when you switch to the batch and back, and after a
        reload. <b>Write listed names in full</b> is under the <b>...</b>
        menu beside this one.
        </p>`,
        { doc: app.ui.help.docUrl('how-it-works/peak-assignment/') }
      )
    "
    :engineReadings="engineReadings"
    :engineTierTooltip="engineTierTooltip"
    :hasLevels="hasLevelColumn"
    :tierCap="tierCap"
    :familySize="isoCount"
    :familyOpen="familyOpen"
    :verdictCell="verdictCell"
    :verdictHelp="{
      title: 'Verification',
      helpKey: 'assignment-verification',
      doc: app.ui.help.docUrl('how-it-works/peak-assignment/#verifying-assignments')
    }"
    dataKey="sample_peak_id"
    v-model:filters="filters"
    selectionMode="single"
    :metaKeySelection="false"
    v-model:selection="selectedRow"
    :rowClass="rowClass"
    :lens="lens"
    @toggle-tier="toggleTier"
    @open-verdict="openVerdict"
    @open-mz="focusPeak"
    @toggle-family="toggleFamily"
    @toggle-group="toggleGroupLine"
    @close-lens="closeLens"
  >
    <template #notices>
      <Message
        v-if="launchError"
        :severity="launchRefused ? 'warn' : 'error'"
        closable
        @close="launchError = null"
      >
        {{ launchError }}
      </Message>
      <Dialog
        v-model:visible="configVisible"
        modal
        header="Assign sample"
        :style="{ width: '26rem' }"
      >
        <PeakAssignConfigForm
          ref="configForm"
          :model="runModel"
          :sample-item-id="app.data.sample.focusedId"
        />
        <Message v-if="formError" severity="error" size="small" data-testid="assign-form-error">
          {{ formError }}
        </Message>
        <template #footer>
          <Button label="Cancel" text severity="secondary" @click="configVisible = false" />
          <Button label="Assign" icon="pi ph ph-magic-wand" :loading="submitting" @click="launch" />
        </template>
      </Dialog>
    </template>

    <!-- The run states with no ledger to show. The run list's error comes
         ahead of the empty state: a run list that failed to load must not read
         as "this sample has none", which invites an Assign run the user does
         not need. The four run states are a set maintained in two files - no
         sample, run list errored, no runs, and a run to show - so a change to
         any of these branches needs AssignmentRunBar's `canAssign` read
         alongside it: the bar hides its Assign sample while the empty state
         carries one. -->
    <template
      v-if="!app.data.sample.focused || runs.error || runs.pending || !runs.list.length"
      #state
    >
      <BaseEmptyState
        v-if="!app.data.sample.focused"
        icon="pi ph ph-cursor-click"
        title="No sample selected"
        hint="Select a sample to view or run its peak assignments."
      />
      <div v-else-if="runs.error" class="center loading-region">
        <BaseLoadError
          :error="runs.error"
          fallback="Could not load the assignment runs for this sample."
          :onRetry="() => runs.load('retry')"
        />
      </div>
      <div v-else-if="runs.pending" class="center loading-region">
        <ProgressSpinner />
      </div>
      <BaseEmptyState
        v-else
        icon="pi ph ph-info"
        title="No assignment runs"
        hint="Assign a composition to every peak in this sample: first from the known target library, then via untargeted composition search."
      >
        <template #actions>
          <Button
            label="Assign sample"
            icon="pi ph ph-magic-wand"
            size="small"
            @click="configVisible = true"
          />
        </template>
      </BaseEmptyState>
    </template>

    <template #strip>
      <!-- The verification queue: a filter of its own, so a chip like the
           tiers, and set off from them at the strip's end. -->
      <button
        type="button"
        class="tier-stat queue-chip"
        :class="{ active: queueMode }"
        :aria-pressed="queueMode"
        data-testid="queue-chip"
        v-tooltip.top="
          queueMode
            ? 'Showing the assignments worth verifying, in the order to ask - click to show every row'
            : 'Show the assignments worth verifying: near a tier edge, random spot checks, and verdicts a later run read differently'
        "
        v-help.top="{
          title: 'Worth verifying',
          helpKey: 'assignment-verification',
          doc: app.ui.help.docUrl('how-it-works/peak-assignment/#the-verification-queue')
        }"
        @click="queueMode = !queueMode"
      >
        <span class="pi ph ph-seal-question" /> <b>{{ queueCount }}</b> to verify
      </button>
    </template>

    <template #view-menu>
      <div class="menu-section">
        <span class="menu-heading">Rows</span>
        <LedgerViewToggle
          v-model="showIsotopologues"
          id="unfold-iso"
          label="Isotopologue lines"
          tooltip="Show isotopologue peaks as indented rows under their compound"
          autofocus
        />
        <LedgerViewToggle
          v-model="view.roles"
          id="show-roles"
          label="Roles"
          tooltip="Show the reagent's and the artifacts' peaks, which are not the sample's compounds"
        />
      </div>
      <div v-if="hasEngineReadings" class="menu-section">
        <span class="menu-heading">Engine readings</span>
        <LedgerViewToggle
          v-model="engineView"
          id="engine-tier-view"
          label="Engine tier and level"
          tooltip="Show the producing engine's own tier and level as a second, outlined chip in the Tier and Level cells, and count and filter the tier chips by its tier"
        />
      </div>
      <LedgerMenuChoice
        v-model="verdictFilter"
        class="verdict-filter"
        label="Verdict"
        ariaLabel="Filter by verification verdict"
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

    <template #above-table>
      <Message
        v-if="staleNotice && !assignments.pending && !assignments.error"
        severity="warn"
        class="stale-notice"
        data-testid="stale-peaks"
      >
        {{ staleNotice }}
      </Message>
    </template>

    <!-- The batch chart draws the Batch peaks ledger's selection; this puts
         the row's species into it, or takes it out, without leaving the
         sample. -->
    <template #lead-column>
      <Column style="width: 2.5rem">
        <template #header>
          <span class="pi ph ph-chart-line" v-tooltip.top="'Plot in the batch chart'" />
        </template>
        <!-- An isotope line is its family's species: it carries no box. -->
        <template #body="{ data }">
          <Checkbox
            v-if="!data.isChild && !data.ledgerKind"
            class="plot-box"
            binary
            :modelValue="chart.isPlotted(data)"
            :disabled="!chart.canPlot(data)"
            :inputId="`plot-${data.peak_assignment_id}`"
            :ariaLabel="
              chart.isPlotted(data) ? 'Remove from the batch chart' : 'Plot in the batch chart'
            "
            v-tooltip.top="
              !chart.canPlot(data)
                ? 'Not in the batch ledger'
                : chart.isPlotted(data)
                  ? 'Remove this species from the batch chart'
                  : 'Plot this species in the batch chart'
            "
            @click.stop
            @update:modelValue="chart.toggle(data)"
          />
        </template>
      </Column>
    </template>

    <!-- An unfolded line: named by its substitution. -->
    <template #line-label="{ data }">
      <span class="child-cell">
        <span class="child-caret">&#8627;</span>
        <span class="child-label" v-tooltip.top="data.isotope_formula || data.isotope_label">{{
          childLabel(data)
        }}</span>
      </span>
    </template>

    <template #formula="{ data }">
      <!-- The formula carries hover-to-copy. -->
      <BaseCopyableField
        v-if="formulaOf(data)"
        class="formula"
        :field="formulaOf(data)"
        :tooltip="data.assigned_formula ? null : ION_TOOLTIP"
      />
    </template>

    <!-- The mechanism, and the xN pill where the sample's rows read the same
         neutral through N channels: it opens the channels together. -->
    <template #ion="{ data }">
      <span class="mech">{{ data.mech || '' }}</span>
      <button
        v-if="channelCount(data) > 1"
        type="button"
        class="channel-pill"
        data-testid="channel-pill"
        :aria-label="`See the ${channelCount(data)} channels of ${data.assigned_formula} together`"
        v-tooltip.top="channelTooltip(data)"
        @click.stop="openChannelLens(data)"
      >
        &times;{{ channelCount(data) }}
      </button>
    </template>

    <template #tier="{ data, cap }">
      <BaseTierTag
        :tier="data.tier"
        :evidence="queueMode ? null : data.evidence"
        :role="data.role"
        :source="data.source"
        :cap="cap"
      />
    </template>

    <!-- What earned the level and what would lift it are in the chip's hover.
         An isotopologue shows its M0's level, which it carries. With Show >
         Engine readings on, the producing engine's own, outlined beside it. -->
    <template #level="{ data }">
      <BaseLevelTag
        v-if="data.shownLevel"
        :level="data.shownLevel.level"
        :evidence="data.shownLevel.evidence"
        :lift="data.shownLevel.lift"
        :role="data.role"
        :source="data.shownLevel.source"
        :engineLevel="data.shownLevel.engineLevel"
      />
      <BaseLevelTag
        v-if="engineReadings && isLevel(data.engine_level)"
        class="engine-chip"
        data-testid="engine-level-chip"
        :level="data.engine_level"
        :tooltip="engineLevelTooltip(data)"
        outlined
      />
    </template>

    <!-- Why the queue asks for the row: near a tier edge, a spot check, or a
         verdict a later run read differently. Only in the queue. -->
    <template #end-columns>
      <Column v-if="queueMode" field="queueRank" style="min-width: 3rem">
        <template #header>
          <span class="pi ph ph-seal-question" v-tooltip.top="'Why the row is worth verifying'" />
        </template>
        <template #body="{ data }">
          <span
            v-if="queueReason(data.queueEntry)"
            class="queue-reason"
            :class="data.queueEntry.reason"
            :data-testid="`queue-reason-${data.queueEntry.reason}`"
            :aria-label="queueReason(data.queueEntry).label"
            v-tooltip.top="queueTooltip(data.queueEntry)"
            ><span :class="queueReason(data.queueEntry).icon"
          /></span>
        </template>
      </Column>
    </template>

    <!-- Curate: the formula search for the row's peak, beside the inspector. -->
    <template #actions="{ data }">
      <Button
        v-if="data.assigned_formula && !isStale(data)"
        class="curate-action"
        icon="pi ph ph-pencil-simple"
        text
        rounded
        size="small"
        severity="secondary"
        aria-label="Curate: search other formulas for this peak"
        v-tooltip.top="'Curate: search other formulas for this peak, and pick one by hand'"
        @click.stop="curate(data)"
      />
    </template>

    <template #popovers>
      <Popover ref="verdictMenu" aria-label="Verification verdict">
        <LedgerVerdictPopover
          v-if="verdictTarget"
          scope="sample"
          :row="verdictTarget"
          :record="verdictFor(verdictTarget)"
          :overlay="overlayFor(verdictTarget)"
          @done="verdictMenu?.hide()"
        />
      </Popover>
    </template>
  </AssignmentLedger>
</template>

<style scoped>
.loading-region {
  width: 100%;
  min-height: 10rem;
}
/* A row whose peak the sample no longer has: there is nothing to focus. */
:deep(tr.stale-peak) {
  opacity: 0.5;
  cursor: default;
}

.queue-reason {
  opacity: 0.75;
}
.queue-reason.stale {
  color: var(--state-warning);
  opacity: 1;
}

.mech {
  font-family: var(--font-mono);
  font-size: 0.82rem;
  opacity: 0.85;
  white-space: nowrap;
}
/* The xN pill: how many channels of the sample read the neutral; it opens
   them together. */
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
}
.channel-pill {
  cursor: pointer;
}
.channel-pill:hover {
  color: var(--p-text-color);
  background: var(--p-content-hover-background);
}
</style>
