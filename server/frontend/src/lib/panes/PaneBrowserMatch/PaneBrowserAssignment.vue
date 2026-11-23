<script setup>
import { ref, reactive, computed, watch, nextTick, onScopeDispose } from 'vue'

import Button from 'primevue/button'
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
import {
  ENGINE_UNSTATED,
  ROLE_BUCKETS,
  bucketOf as bucketFor,
  bucketRank,
  engineBucketOf,
  tierName,
  tierRank
} from '@/lib/tiers'
import { isLevel, levelRank } from '@/lib/levels'
import { queueReason, queueRows, queueTooltip } from '@/lib/verificationQueue'
import { scrollVirtualRowIntoView } from '@/lib/virtualScroll'
import {
  AssignmentLedger,
  LedgerMenuChoice,
  LedgerVerdictPopover,
  LedgerViewToggle,
  ledgerTierChips,
  runSearchWindow,
  sampleLineLabel,
  useLedgerFilters,
  useLedgerSort,
  useLedgerView,
  useRowPopover,
  verdictRank
} from '@/lib/ledger'
import { useApp } from '@/stores'

import { useAssignmentLauncher } from './stores'
import { useBatchChartSelection } from './stores/batchChartSelection.js'

/**
 * The sample scope of the assignment ledger (lib/ledger/AssignmentLedger.vue):
 * every peak of the focused sample with its committed assignment from the
 * selected run. This pane is the scope's configuration - its rows, its extra
 * columns (the chart toggle, the ionization, the queue's reason), its strip
 * items (the verification queue) and its single selection, tied to the
 * focused peak - and owns the Assign sample dialog.
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
const hasLevels = computed(() => assignments.value.list.some((row) => isLevel(row.level)))

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
// Seeded again on every open, so last time's override is not carried.
const assignParams = usePeakAssignParams()
const runModel = reactive(Object.fromEntries(PARAM_KEYS.map((key) => [key, null])))
const configForm = ref(null)

function seedRun() {
  const saved = app.data.batch.focused?.assignment_settings ?? null
  Object.assign(runModel, batchSettingsModel(saved, assignParams.defaults))
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

// The configuration carries between opens - it is the user's, held in the
// store and persisted - so only the stale refusal is cleared here. Also scope
// help mode to the dialog's cards while it is open.
watch(configVisible, (open) => {
  if (open) {
    launchError.value = null
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
  // keeps the dialog open on the field that says so.
  if (configForm.value && !configForm.value.commit()) return
  submitting.value = true
  try {
    await runs.value.assign(sampleItemId, runConfig(runModel))
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
function focusPeak(assignment) {
  const peak = app.data.peak.list.find(
    (p) => String(p.peak_id) === String(assignment.sample_peak_id)
  )
  if (peak) {
    app.data.peak.focus(peak)
    app.ui.tab.active = 'sample'
  }
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
const rowClass = (row) => (isStale(row) ? 'stale-peak' : null)

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

// --- One row per neutral --------------------------------------------------------

// A run that searches several ionization channels can commit the same neutral
// through more than one of them - [M+H]+ and [M+NH4]+, [M-H]- and [M+Br]- - and
// the ledger lists each as a row of its own, at its own m/z. Grouped, the
// neutral is one row: its strongest reading heads it, by the ledger's resting
// order (tier, then fit), and the others sit under it, shown when the reader
// opens the group from the head's ionization cell. Off by default: a row per
// ion is what a reader matching the ledger against the spectrum needs.
//
// Grouped on what the rows show, so the filters apply first. Only an analyte's
// formula groups; a source ion or a peak nothing explained names no neutral.
// Show > Group by: none, or compound. The batch's groups (by series) are the
// batch scope's.
const groupByFormula = computed({
  get: () => view.groupBy === 'compound',
  set: (on) => (view.groupBy = on ? 'compound' : 'none')
})
const GROUP_BY = [
  { value: 'none', label: 'None' },
  {
    value: 'compound',
    label: 'Compound',
    tooltip:
      'One row per neutral formula: its other ionization channels fold under its strongest reading'
  }
]
// The groups the reader opened, by neutral. Kept while grouping stays on, so a
// re-sort or a filter does not close them.
const openGroups = reactive(new Set())
const toggleGroup = (key) => {
  if (openGroups.has(key)) openGroups.delete(key)
  else openGroups.add(key)
}
watch(groupByFormula, (on) => {
  if (!on) openGroups.clear()
})
const groupKeyOf = (row) =>
  row.assigned_formula && !ROLE_BUCKETS.includes(row.role) ? neutralKey(row.assigned_formula) : ''
// What the head's toggle says: the channels it would show, and what clicking
// does.
const groupTooltip = (row) => {
  const channels = (row.channelRows ?? []).map((channel) => channel.mech ?? 'another channel')
  const shown = openGroups.has(row.groupKey)
  return (
    `Also read through ${channels.join(', ')}: the same neutral, as ` +
    `${channels.length === 1 ? 'another ion' : 'other ions'}. Click to ` +
    `${shown ? 'fold' : 'show'} ${channels.length === 1 ? 'it' : 'them'} under this row`
  )
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
} = useLedgerSort('sample', {
  assigned_formula: (row) => formulaOf(row),
  listing: (row) =>
    row.target ? targetName(row.target) : row.listing ? listingName(row.listing) : null
})

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

// What a row of the run shows of the channels that committed its neutral: the
// ledger-measured channel count first - it reaches every committed row, where
// the curated per-compound count reaches only what Stage A claimed - and the
// curated one where it is all there is. Where both exist the first is a
// superset of the second, so preferring it never shrinks the marker.
const channelCount = (row) =>
  row.corroboration_channels ??
  row.provenance?.cross_channel?.channels?.length ??
  row.corroboration_adducts ??
  row.provenance?.corroboration?.n_adducts

// The isotope lines of a row, as rows under it: an analyte's isotopologues, a
// source ion's heavier lines (isIsotopeLine), ordered by m/z among themselves.
// `underChannel` marks the lines of a grouped channel's row, indented a step
// further than the row they follow.
function isotopologuesOf(parent, underChannel = false) {
  return assignments.value
    .childrenOf(parent.peak_assignment_id)
    .slice()
    .sort((a, b) => (a.sample_peak_mz ?? 0) - (b.sample_peak_mz ?? 0))
    .map((child) => {
      // Corroboration is written onto the M0 winner alone: an isotopologue is
      // the same ion measured at another isotope, so it shows its parent's
      // count, and the marker says where it came from.
      const own = channelCount(child)
      return {
        ...child,
        // Only the parents are sorted - children are spliced in under their
        // parent whatever the sort - so a family sorts as one block.
        tierRank: parent.tierRank,
        // An isotopologue is its M0's formula measured again; the listing is
        // on the M0's row, not repeated on each line.
        listing: null,
        corrobAdducts: own ?? parent.corrobAdducts,
        // True whenever the count on this row is the parent's, independent of
        // whether it clears the marker's threshold.
        corrobInherited: own == null && parent.corrobAdducts > 0,
        mech: mechById.value.get(child.ionization_mechanism_id) ?? null,
        isChild: true,
        underChannel
      }
    })
}

// Table rows. Parents (M0 + unassigned/reagent) are filtered by the active
// chips, grouped by neutral when that is on, then ordered by the sorted column
// with confidence breaking ties - or, in the verification queue, the queue's
// rows in the queue's order. When unfolded, each parent's isotope lines are
// inserted right after it, and an open group's other channels after those, each
// with its own lines - a family is one block wherever its parent lands, which
// is the only arrangement in which the indented child rows can be read at all.
const rows = computed(() => {
  const ledger = assignments.value.list.filter((row) => !isIsotopeLine(row))
  const parents = (queueMode.value ? queueRows(ledger, queue.value?.list) : ledger)
    .filter((row) => passes(row, { verdicts: !queueMode.value }))
    .map((row) => {
      const listing = ledgerListing(row.reference_listing)
      return {
        ...row,
        tierRank: bucketRank(row.tier, row.role),
        // Null where the producing engine stated no tier, so those rows sort
        // last in both directions: "this engine said nothing" is not a position
        // on the scale.
        engineTierRank: row.engine_tier != null ? tierRank(row.engine_tier) : null,
        // Null rather than a rank where the row carries no level, likewise.
        levelRank: isLevel(row.level) ? levelRank(row.level) : null,
        verdictRank: verdictRank(effectiveVerdictFor(row)),
        listing,
        target: targetOf(row.reference_listing, listing),
        corrobAdducts: channelCount(row) ?? 0,
        corrobInherited: false,
        mech: mechById.value.get(row.ionization_mechanism_id) ?? null,
        isChild: false,
        isChannel: false
      }
    })

  // One row per neutral: the strongest reading heads its group and carries the
  // others, which leave the top level.
  let heads = parents
  if (groupByFormula.value) {
    const groups = new Map()
    for (const row of parents) {
      const key = groupKeyOf(row)
      if (!key) continue
      if (groups.has(key)) groups.get(key).push(row)
      else groups.set(key, [row])
    }
    const grouped = new Set()
    for (const [key, group] of groups) {
      if (group.length < 2) continue
      group.sort(byConfidence)
      const [head, ...others] = group
      head.groupKey = key
      head.channelRows = others.map((row) => ({ ...row, isChannel: true }))
      for (const row of others) grouped.add(row)
    }
    heads = parents.filter((row) => !grouped.has(row))
  }

  sort(heads, queueMode.value ? byQueue : byConfidence)

  const result = []
  for (const head of heads) {
    result.push(head)
    if (showIsotopologues.value) result.push(...isotopologuesOf(head))
    if (head.channelRows && openGroups.has(head.groupKey)) {
      for (const channel of head.channelRows) {
        result.push(channel)
        if (showIsotopologues.value) result.push(...isotopologuesOf(channel, true))
      }
    }
  }
  return result
})

// The head each grouped row sits under, so a peak focused elsewhere finds its
// row even while its group is folded.
const headOfGrouped = computed(() => {
  const heads = new Map()
  for (const row of rows.value) {
    for (const channel of row.channelRows ?? []) {
      heads.set(channel.peak_assignment_id, row.peak_assignment_id)
    }
  }
  return heads
})

// An unfolded line's label: its substitution wherever the row names one,
// counted from the family's M0 (lib/ledger/rows.js).
const childLabel = (row) => sampleLineLabel(row, assignments.value.m0Of(row))

// A borrowed count is parenthesised, so an isotopologue does not read at a glance as
// a peak seen through several adducts in its own right.
const corrobLabel = (row) =>
  row.corrobInherited ? `(${row.corrobAdducts})` : `${row.corrobAdducts}`

// Tooltip for the corroboration marker, beside the ionization it counts
// channels of. An isotopologue shows the count its M0 was corroborated by, so
// it says the evidence is the family's. No calibrated probability is named:
// it is read off a curve nobody has verified yet (step 3.4d of the plan).
const corrobTooltip = (row) =>
  row.corrobInherited
    ? `Supported by ${row.corrobAdducts} channels, via the M0 of this isotopologue family`
    : `Supported by ${row.corrobAdducts} channels`

// Two-way selection tied to the focused peak: clicking a row focuses its peak,
// and focusing a peak elsewhere (spectrum click, inspector) highlights its row.
// When a folded-out isotopologue is focused, highlight its M0 row.
const selectedRow = computed({
  get: () => {
    const focused = app.data.peak.focused
    if (!focused) return null
    // Prefer the exact row for the focused peak (present for M0/standalone rows,
    // and for isotopologue children when unfolded).
    const exact = rows.value.find((r) => String(r.sample_peak_id) === String(focused.peak_id))
    if (exact) return exact
    // Folded: a focused isotopologue child maps to its M0 row, and a row of a
    // folded group - or a line of one - to the group's head.
    const assignment = assignments.value.forPeak(focused.peak_id)
    const ownerId = isIsotopeLine(assignment)
      ? assignment.owner_peak_assignment_id
      : (assignment?.peak_assignment_id ?? null)
    if (ownerId == null) return null
    const rowOf = (id) => (id != null ? rows.value.find((r) => r.peak_assignment_id === id) : null)
    return rowOf(ownerId) ?? rowOf(headOfGrouped.value.get(ownerId)) ?? null
  },
  set: (row) => {
    // Clicking the selected row again de-selects it, and PrimeVue says so by
    // emitting null. Selection lives in the peak store, so unfocusing there
    // clears the spectrum highlight, the inspector and the timeseries too.
    if (row) focusPeak(row)
    else app.data.peak.unfocus()
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
  nextTick(() => scrollVirtualRowIntoView(ledgerRef.value?.table, index))
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

// Isotopologues folded under a formula's M0.
const isoCount = (row) => assignments.value.childrenOf(row.peak_assignment_id).length

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
  listing: 'listing',
  level: 'level',
  levelSort: 'levelRank'
}
const HEADERS = {
  listing:
    'What a reference list calls the formula: the first name the run matched from a list, ' +
    "that list, and the list's tags. A formula match names candidate compounds; it is not an " +
    'identification.',
  engineTier: "The producing engine's own tier, beside this server's",
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
    :rowHeight="35.5"
    v-model:sortField="sortField"
    v-model:sortOrder="sortOrder"
    :fields="FIELDS"
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
        folded into the <b>+N</b> marker by default - as indented rows under
        their main peak (M0), each named by its substitution. <b>Roles</b>
        shows the reagent's and the artifacts' peaks; off, the ledger lists
        the sample's compounds and the peaks nothing explained.
        </p>
        <p>
        <b>Verdict</b> narrows the table to one verification verdict.
        <b>Group by</b> compound makes a neutral read through several
        ionization channels one row, headed by its strongest reading; the
        arrow beside its ionization shows the others under it.
        </p>
        <p>
        On a run imported from another engine that states tiers of its
        own, <b>Engine tier</b> makes the tier chips count and filter by
        that engine's tier, with <b>not stated</b> for the formulas it
        gave none. The tier column and the sort stay on Mascope's tier.
        </p>
        <p>
        Every choice here, the search and the tier chips are kept for this
        ledger: they hold when you switch to the batch and back, and after a
        reload. <b>Write listed names in full</b> is under the <b>...</b>
        menu beside this one.
        </p>`,
        { doc: app.ui.help.docUrl('how-it-works/peak-assignment/') }
      )
    "
    :hasEngineTiers="hasEngineTiers"
    :engineTierTooltip="engineTierTooltip"
    :hasLevels="hasLevels"
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
    @toggle-tier="toggleTier"
    @open-verdict="openVerdict"
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
      <div v-if="hasEngineTiers" class="menu-section">
        <span class="menu-heading">Engine readings</span>
        <LedgerViewToggle
          v-model="engineView"
          id="engine-tier-view"
          label="Engine tier"
          tooltip="Count and filter the tier chips by the producing engine's own tier, not this server's"
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
      <Column style="min-width: 3rem">
        <template #header>
          <span class="pi ph ph-chart-line" v-tooltip.top="'In the batch chart'" />
        </template>
        <template #body="{ data }">
          <button
            type="button"
            class="chart-cell"
            :class="{ plotted: chart.isPlotted(data) }"
            :disabled="!chart.canPlot(data)"
            :aria-label="
              chart.isPlotted(data) ? 'Remove from the batch chart' : 'Plot in the batch chart'
            "
            v-tooltip.top="
              !chart.canPlot(data)
                ? 'Not in the batch ledger'
                : chart.isPlotted(data)
                  ? 'Remove this species from the batch chart'
                  : 'Plot this species in the batch chart'
            "
            @click.stop="chart.toggle(data)"
          >
            <span class="pi ph ph-chart-line" />
          </button>
        </template>
      </Column>
    </template>

    <template #formula="{ data }">
      <span v-if="data.isChild" class="child-cell" :class="{ deeper: data.underChannel }">
        <span class="child-caret">&#8627;</span>
        <span class="child-label" v-tooltip.top="data.isotope_formula || data.isotope_label">{{
          childLabel(data)
        }}</span>
      </span>
      <!-- The same neutral read through another channel, under the row that
           heads its group: indented, and named again so the row reads on its
           own. -->
      <span
        v-else-if="data.isChannel"
        class="child-cell channel-cell"
        v-tooltip.top="'The same neutral, read through another ionization channel'"
      >
        <span class="child-caret">&#8627;</span>
        <span class="formula">{{ formulaOf(data) }}</span>
        <span v-if="isoCount(data)" class="iso-count">+{{ isoCount(data) }}</span>
      </span>
      <!-- The formula carries hover-to-copy; the isotopologue count rides in the
           slot, outside what gets copied. -->
      <BaseCopyableField
        v-else-if="formulaOf(data)"
        class="formula"
        :field="formulaOf(data)"
        :tooltip="data.assigned_formula ? null : ION_TOOLTIP"
      >
        <span
          v-if="isoCount(data)"
          class="iso-count"
          v-tooltip.top="`${isoCount(data)} isotopologue peak${isoCount(data) === 1 ? '' : 's'}`"
          >+{{ isoCount(data) }}</span
        >
      </BaseCopyableField>
    </template>

    <template #middle-columns>
      <Column field="mech" sortable style="min-width: 5rem">
        <template #header>
          <span v-tooltip.top="'Ionization mechanism (adduct)'">Ionization</span>
        </template>
        <template #body="{ data }">
          <span class="mech">{{ data.mech || '' }}</span>
          <!-- How many of the run's channels committed the row's neutral:
               evidence for the formula, beside the channel it was read
               through. -->
          <span
            v-if="data.corrobAdducts > 1"
            class="corrob-mark"
            v-tooltip.top="corrobTooltip(data)"
            ><span class="pi ph ph-link-simple" />{{ corrobLabel(data) }}</span
          >
          <!-- A group's head opens its other channels here. -->
          <button
            v-if="data.channelRows?.length"
            type="button"
            class="group-toggle"
            data-testid="group-toggle"
            :aria-expanded="openGroups.has(data.groupKey)"
            :aria-label="
              `${openGroups.has(data.groupKey) ? 'Fold' : 'Show'} the other ` +
              `${data.channelRows.length === 1 ? 'channel' : 'channels'} of ${data.assigned_formula}`
            "
            v-tooltip.top="groupTooltip(data)"
            @click.stop="toggleGroup(data.groupKey)"
          >
            <span
              :class="[
                'pi',
                openGroups.has(data.groupKey) ? 'pi-chevron-down' : 'pi-chevron-right'
              ]"
            />+{{ data.channelRows.length }}
          </button>
        </template>
      </Column>
    </template>

    <template #tier="{ data }">
      <BaseTierTag
        :tier="data.tier"
        :evidence="queueMode ? null : data.evidence"
        :role="data.role"
        :source="data.source"
      />
    </template>

    <!-- What earned the level and what would lift it are in the chip's hover.
         An isotopologue shows its M0's level, which it carries. -->
    <template #level="{ data }">
      <BaseLevelTag
        v-if="isLevel(data.level)"
        :level="data.level"
        :evidence="data.level_evidence"
        :lift="data.level_lift"
        :role="data.role"
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
/* Channel-corroboration marker beside the ionization. */
.corrob-mark {
  display: inline-flex;
  align-items: center;
  gap: 0.1rem;
  margin-left: 0.4rem;
  font-size: 0.7rem;
  font-variant-numeric: tabular-nums;
  color: var(--state-info);
}
.corrob-mark .pi {
  font-size: 0.75rem;
}
/* An isotopologue of a grouped channel's row, a step under that row. */
.child-cell.deeper {
  padding-left: 1.8rem;
}
.channel-cell .formula {
  opacity: 0.8;
}
/* The head's toggle for its other channels: quiet until hovered. */
.group-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.1rem;
  margin-left: 0.4rem;
  padding: 0 0.25rem;
  border: 1px solid var(--p-content-border-color, #e3e6ec);
  border-radius: 4px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 0.7rem;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}
.group-toggle .pi {
  font-size: 0.6rem;
}
.group-toggle:hover {
  background: var(--p-content-hover-background);
}
/* The chart cell: faint until hovered, lit in the accent colour while the
   species is in the batch chart, greyed when the peak is not in the ledger. */
.chart-cell {
  padding: 0.1rem 0.3rem;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
  opacity: 0.45;
}
.chart-cell:hover {
  opacity: 1;
  background: var(--p-content-hover-background);
}
.chart-cell.plotted {
  color: var(--p-primary-color);
  opacity: 1;
}
.chart-cell:disabled {
  cursor: default;
  opacity: 0.15;
}
</style>
