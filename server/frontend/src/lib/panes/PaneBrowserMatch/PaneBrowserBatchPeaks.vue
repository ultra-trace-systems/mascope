<script setup>
import { ref, computed, watch, nextTick } from 'vue'

import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Popover from 'primevue/popover'
import ProgressSpinner from 'primevue/progressspinner'
import Select from 'primevue/select'
import ToggleSwitch from 'primevue/toggleswitch'
import { FilterMatchMode, FilterOperator, FilterService } from '@primevue/core/api'

import { BaseTabbedPanel, BaseTierTag, BaseCopyableField, BaseVerdictBadge } from '@/lib/base'
import { num } from '@/lib/formatters'
import { TIERS, TIER_META, countTiers, tierRank } from '@/lib/tiers'
import { VERDICT_META } from '@/lib/verification'
import { prettyTrim } from '@/lib/utils'
import { useApp } from '@/stores'

import { useBatchPeakJump } from './stores/batchPeakJump.js'
import { MAX_SELECTED_BATCH_PEAKS } from '@/stores/data/modules/batchPeak/ledger'

import { useBatchPeakCompute } from './stores/batchPeakCompute.js'
import BatchPeakVerdictPopover from './BatchPeakVerdictPopover.vue'

/**
 * Batch-peak ledger: the selection surface for the peak-centric batch overview.
 * A virtual-scrolled, multi-select table of the batch's batch peaks (cross-sample
 * m/z anchors); the rows the user selects here are exactly what the Assignments
 * chart plots, so the chart never renders 1000+ traces at once.
 */
const app = useApp()
// The row action that opens the brightest sample on this peak.
const jump = useBatchPeakJump()

// The button that launches this lives a row up, in the browser's switch bar
// (BatchPeakComputeBar.vue), so the launch and its state are shared through a
// store. Only the refusal is read back here, to be reported below the table it
// is about rather than in a toast that has scrolled away.
const compute = useBatchPeakCompute()

const ledger = computed(() => app.data.batchPeak)
const verdicts = computed(() => app.data.batchPeakVerification)
// The batch's runs: which one the ledger is reading, and whether that is the
// live one. An earlier run is history - shown from its snapshot, read-only.
const runs = computed(() => app.data.batchPeakRun)
const RUN_ACTION_LABELS = {
  fold: 'folded samples',
  rebuild: 'a rebuild',
  search_untargeted: 'an untargeted search',
  import: 'an import'
}
const viewedRunLabel = computed(() => {
  const run = runs.value.viewing
  return run ? (RUN_ACTION_LABELS[run.action] ?? run.action) : ''
})

// --- Batch-level verdicts ----------------------------------------------------
// One judgment per species at a batch peak, recorded from the Verdict column's
// popover. A row's badge is the live verdict on its present claim, or - stale -
// the latest live one on a claim the consensus has since left; the projection
// carries a rank for the column to sort on, confirmed first and unjudged last,
// the way the tier column sorts on `tierRank`.
const VERDICT_RANK = { confirmed: 0, rejected: 1, unsure: 2 }
const verdictFor = (row) => verdicts.value.forAnchor(row)
const verdictStale = (row) => {
  const record = verdictFor(row)
  return Boolean(record && verdicts.value.isStale(record, row))
}
const verdictRank = (row) => {
  const record = verdictFor(row)
  return record ? (VERDICT_RANK[record.verdict] ?? 3) : null
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

// A Popover rather than a Menu, matching the sample ledger: a menu item cannot
// hold a labelled switch, and the switch's only accessible name is its label.
const viewMenu = ref()
const viewMenuOpen = ref(false)
const toggleViewMenu = (event) => viewMenu.value?.toggle(event)

const filters = ref({
  consensus_formula: {
    operator: FilterOperator.AND,
    constraints: [{ value: null, matchMode: FilterMatchMode.CONTAINS }]
  },
  consensus_tier: {
    operator: FilterOperator.AND,
    constraints: [{ value: null, matchMode: FilterMatchMode.EQUALS }]
  }
})

const breadcrumb = computed(() => {
  const batch = app.data.batch.focused
  if (!batch) return null
  const folded = ledger.value.list.length - parents.value.length
  return {
    items: [
      {
        icon: 'pi pi-hashtag',
        label: prettyTrim(batch.sample_batch_name, 25),
        disabled: true,
        tooltip: `Batch peaks for ${batch.sample_batch_name}`
      },
      {
        icon: 'pi ph ph-atom',
        // Species, not anchors: the count matches the tier strip below it and
        // the rows the table shows at top level, which is what a reader
        // comparing the two would otherwise find disagreeing by the size of
        // the isotopologue tail.
        label: `${parents.value.length} batch peaks`,
        disabled: true,
        tooltip: folded
          ? `${parents.value.length} species; ${folded} isotopologue ` +
            `peak${folded === 1 ? '' : 's'} shown under their main peak`
          : undefined
      }
    ]
  }
})

// --- Isotopologue families --------------------------------------------------

// Batch peaks are bare m/z anchors, so the family link is one the backend
// derives from the members' per-sample assignments and hands over as
// `isotopologue_of` - the anchor whose M0 this one is an isotopologue of.
//
// It is deliberately ONE hop, observed rather than reconciled, which leaves the
// reader two things to settle and the whole ledger in hand to settle them with:
// a chain (an isotopologue of an isotopologue) has to reach a row that is actually
// drawn, and a link out of the list - to an anchor the server filtered out, or
// one deleted since - has to fail into a top-level row rather than into a row
// that is never rendered at all.
const byId = computed(() => new Map(ledger.value.list.map((bp) => [bp.batch_peak_id, bp])))

/**
 * The row this one folds under, or null when it stands on its own.
 *
 * Walks up to the family's root so the table stays two levels deep, which is
 * what lets it keep a fixed row height and virtual-scroll. A cycle - which no
 * fold should produce and no ledger should have to trust it did not - ends the
 * walk with no parent, so the row is drawn rather than lost.
 */
const rootParentId = (row, index) => {
  const seen = new Set([row.batch_peak_id])
  let parentId = row.isotopologue_of
  while (parentId && !seen.has(parentId)) {
    const parent = index.get(parentId)
    if (!parent) return null
    if (!parent.isotopologue_of) return parentId
    seen.add(parentId)
    parentId = parent.isotopologue_of
  }
  return null
}

// The ledger's records with the tier's confidence rank attached, which is what
// the tier column sorts on: the raw tier string sorts alphabetically, and
// "below_assignability" before "candidate" is not an ordering anyone asked for.
const decorated = computed(() => {
  const index = byId.value
  return ledger.value.list.map((batchPeak) => ({
    ...batchPeak,
    tierRank: tierRank(batchPeak.consensus_tier),
    verdictRank: verdictRank(batchPeak),
    parentId: rootParentId(batchPeak, index)
  }))
})

// Isotopologues by the row they fold under, ordered by m/z among themselves -
// which is the order an isotopologue family reads in, M0 first and then M+1,
// M+2.
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

// One verdict popover for the table, pointed at the row whose cell opened it -
// the live row, so a reload under an open popover (the consensus moved, say)
// shows the row as it now is. Clicking the open row's cell again closes it;
// another row's cell moves it there.
const verdictMenu = ref()
const verdictRowId = ref(null)
const verdictTarget = computed(() =>
  verdictRowId.value
    ? (decorated.value.find((row) => row.batch_peak_id === verdictRowId.value) ?? null)
    : null
)
function openVerdict(event, row) {
  const menu = verdictMenu.value
  if (!menu) return
  if (verdictRowId.value === row.batch_peak_id) {
    menu.toggle(event)
    return
  }
  verdictRowId.value = row.batch_peak_id
  menu.hide()
  nextTick(() => menu.show(event))
}

const isotopologueCount = (row) => childrenByParent.value.get(row.batch_peak_id)?.length ?? 0

// Label for an unfolded isotopologue row. A batch peak carries no isotope formula
// - it is an m/z anchor, and the per-sample rows behind it may not even agree
// on one - so the label is the nominal mass offset from the M0 it folds under,
// the same "M+1" the sample ledger's engine writes.
const childLabel = (row) => {
  const parent = byId.value.get(row.parentId)
  if (!parent) return 'iso'
  const offset = Math.round(row.mz - parent.mz)
  if (offset > 0) return `M+${offset}`
  return offset < 0 ? `M${offset}` : 'M0'
}

// Fold isotopologues under their M0 by default: an isotopologue carries its family's
// formula, so unfolded it reads as a second row for one compound.
const showIsotopologues = ref(false)

// --- Sorting and filtering --------------------------------------------------

// Both are the pane's, not the table's. PrimeVue sorts and filters the flat row
// array it is handed, which tears every isotopologue away from the parent it
// belongs under the moment the ledger is sorted by anything but the default -
// sorting by intensity drops an isotopologue hundreds of rows below the "+N" that
// counts it, where an indented "M+1" means nothing. `lazy` hands both back to
// us so `rows` can order the parents and re-attach each family underneath. The
// same choice, for the same reason, as the sample ledger's.
//
// The header still renders its sort indicator and still emits the field it was
// clicked with, and the filter menus still write into `filters`;
// `removableSort` gives a third click that clears the column and returns the
// ledger to its confidence-ordered default.
const sortField = ref('n_present')
const sortOrder = ref(-1)

// Numeric collation, so the formula column reads as a chemist expects: C2H6
// before C10H22, not after it. This is the comparer PrimeVue sorted with -
// @primeuix/utils' localeComparator() is
// `new Intl.Collator(undefined, { numeric: true }).compare` - and taking the
// sort over must not quietly change the order it used to produce.
const collator = new Intl.Collator(undefined, { numeric: true })

// Missing values sort last in both directions - a peak with no formula, or no
// intensity, is unknown rather than "smallest" - counting the empty string as
// missing, which is what PrimeVue's isEmpty() did.
const isBlank = (value) => value == null || value === ''

function compareBy(field, order) {
  const dir = order === -1 ? -1 : 1
  return (a, b) => {
    const av = a[field]
    const bv = b[field]
    if (isBlank(av) && isBlank(bv)) return 0
    if (isBlank(av)) return 1
    if (isBlank(bv)) return -1
    if (typeof av === 'string' && typeof bv === 'string') return collator.compare(av, bv) * dir
    if (av < bv) return -dir
    if (av > bv) return dir
    return 0
  }
}

// The ledger's resting order: most confident first, best fit first within a
// tier. Also the tie-breaker under every other column, and what an unsorted
// table (third click on a sorted header) falls back to.
const byConfidence = (a, b) =>
  a.tierRank - b.tierRank || (b.best_fit_score ?? -1) - (a.best_fit_score ?? -1)

/**
 * PrimeVue's column filtering, applied here because `lazy` hands it back.
 *
 * The same rules it applied itself, so the menus keep meaning what they say: a
 * constraint whose value is null is not a filter, the operator joins the rest,
 * and the comparison is PrimeVue's own FilterService - so a match mode chosen
 * in the menu ("Starts with", "Not contains") is honoured rather than
 * approximated by whichever one this pane happened to hard-code.
 */
const passesFilters = (row) => {
  for (const [field, meta] of Object.entries(filters.value)) {
    const constraints = (meta.constraints ?? [meta]).filter(
      (constraint) => constraint.value !== null
    )
    if (!constraints.length) continue
    const matches = (constraint) =>
      FilterService.filters[constraint.matchMode ?? FilterMatchMode.STARTS_WITH](
        row[field],
        constraint.value
      )
    const passed =
      meta.operator === FilterOperator.OR ? constraints.some(matches) : constraints.every(matches)
    if (!passed) return false
  }
  return true
}

// --- Rows -------------------------------------------------------------------

// Parents are filtered by the menus and the chips, then ordered by the sorted
// column with confidence breaking ties. When unfolded, each parent's isotopologues
// are inserted right after it - a family is one block wherever its parent
// lands, which is the only arrangement in which the indented rows can be read
// at all. The isotopologues ride with their parent rather than being filtered
// themselves: a family shares one formula and one tier, so a filter that kept
// the parent kept the family.
const rows = computed(() => {
  const visible = parents.value.filter(passesFilters)
  const byColumn = sortField.value ? compareBy(sortField.value, sortOrder.value) : null
  visible.sort(byColumn ? (a, b) => byColumn(a, b) || byConfidence(a, b) : byConfidence)
  if (!showIsotopologues.value) return visible

  const result = []
  for (const parent of visible) {
    result.push(parent)
    result.push(...(childrenByParent.value.get(parent.batch_peak_id) ?? []))
  }
  return result
})

// --- Selection --------------------------------------------------------------

// How many rows the last bulk write wanted, when that was more than the cap
// allows; 0 when it fit. Counting the rows rather than raising a flag is what
// lets the notice say how much of the ledger was left out.
const overflowFrom = ref(0)

// Set instead when a single row was refused because the selection was already
// full. The two are worth telling apart: a row click hands over the selection
// with the clicked row appended, so it arrives exactly one over the cap and the
// slice drops the very row that was clicked. That is a refusal, not a select-all
// whose tail was cut off, and reporting it as "300 of the 301 matching rows"
// would name a number that is not the size of anything the user can see.
const selectionAtCapacity = ref(false)

// The rows "all" means: filtered, sorted and folded as displayed. This used to
// be read back off the table, which under `lazy` hands back the array it was
// given - so it was `rows` by a longer route, and a route that could only ever
// disagree by being wrong.
const selectableRows = () => rows.value

/**
 * The single write into the ledger selection, capped.
 *
 * Every route into the selection lands here - the header checkbox, Ctrl+A, a
 * shift-click range, and an ordinary row click - because each of the first
 * three hands over the whole filtered row set in one go. A cap on only the one
 * we wrote ourselves would leave the other doors to the same ten thousand rows
 * standing open.
 *
 * Always assigns a fresh array: the header checkbox hands over the table's own
 * live `processedData`, which must not be truncated in place.
 */
const applySelection = (selection) => {
  // Shift+Space hands over a bare row rather than an array of one when the
  // range it would select collapses onto the row already focused.
  const wanted = Array.isArray(selection) ? selection : [selection]
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
 * Taking it over from the table is what keeps the checkbox usable at the cap:
 * left to itself it looks for every filtered row in the selection, never finds
 * them all, and so offers only to re-select the same rows - leaving no gesture
 * that empties the selection.
 *
 * It has to be the same membership test, though, over the rows "select all"
 * would actually write. A count would read as checked whenever the selection
 * happened to be as large as the filtered set, even with no row in common -
 * so narrowing the filter after a large selection would tick a box over rows
 * that are all unselected, and clicking it would clear the selection rather
 * than fill it. Bounded by the cap, so the test stays cheap on a large ledger.
 */
const allSelected = computed(() => {
  const selectable = selectableRows()
  if (!selectable.length) return false
  const held = new Set(ledger.value.selected.map((row) => row.batch_peak_id))
  return selectable.slice(0, MAX_SELECTED_BATCH_PEAKS).every((row) => held.has(row.batch_peak_id))
})

/**
 * What to tell the user about the size of their selection, or null.
 */
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

// While folded, no isotopologue is selected.
//
// A selection is what the chart plots, so an isotopologue left in it while its row
// is folded away draws a trace with no ticked row behind it, and spends the cap
// on a row the user can no longer see to release. Only the fold does this - the
// rows a tier chip hides are a filter rather than a fold, and a filter has never
// cost the selection anything.
//
// Written as an invariant rather than as a reaction to the toggle, because the
// toggle is the pane's state and the selection is the store's: the pane remounts
// folded while the selection survives, so an isotopologue ticked before a tab switch
// would come back plotted, uncounted, and with no row left to untick it from.
// Unfolding takes nothing back - the isotopologues reappear unselected, where they
// were before anyone ticked them.
watch(
  [showIsotopologues, decorated],
  ([unfolded]) => {
    // Re-checked on every ledger reload, so it leaves early on the two states
    // it has nothing to do in rather than walking the whole list to find out.
    if (unfolded || !ledger.value.selected.length) return
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

// The chips and the tier column's filter menu drive the same filter rather than
// two: a chip writes the constraint the menu reads, so the two controls cannot
// contradict each other and leave the table empty for a reason only one of them
// is showing. That does make the chips single-select, unlike the sample
// ledger's - one EQUALS constraint holds one tier - which is the price of the
// two staying honest about each other.
const tierConstraint = computed(() => filters.value.consensus_tier.constraints[0])
const activeTier = computed(() => tierConstraint.value.value)
const toggleTier = (tier) => {
  tierConstraint.value.value = activeTier.value === tier ? null : tier
}

// A chip is a filter control, so its number has to be the number of rows
// clicking it produces: counted over `parents`, the same population the table
// shows at top level and the breadcrumb names. Isotopologues are left out for the
// reason the sample ledger leaves out its iso_child rows - an isotopologue carries
// its family's formula and tier, so counting it counts one species twice.
//
// Counted over the whole ledger rather than the filtered rows, as the sample
// pane's are: a histogram that reacted to its own filter would collapse to one
// non-zero bucket the moment it was used.
const tierCounts = computed(() => countTiers(parents.value, (bp) => bp.consensus_tier))

// One chip per tier in confidence order, counts included.
const tierChips = computed(() =>
  TIERS.map((tier) => ({
    key: tier,
    label: TIER_META[tier].label,
    count: tierCounts.value[tier] ?? 0
  }))
)

// --- Intensity --------------------------------------------------------------

// What the intensity column reports, spelled out where it is shown: the ledger
// has one number per species and a reader has to be told which of the batch's
// samples it came from. The unit is the batch peak's own - heights on an
// Orbitrap, areas on a TOF - so it is read off the rows rather than assumed.
const INTENSITY_UNITS = {
  sum_peak_heights: 'summed peak height',
  sum_peak_areas: 'summed peak area'
}

const intensityTooltip = computed(() => {
  const variable = ledger.value.list.find((bp) => bp.intensity_variable)?.intensity_variable
  const unit = INTENSITY_UNITS[variable]
  return (
    'Highest intensity this species reaches in any sample of the batch' + (unit ? ` (${unit})` : '')
  )
})

// Whatever was in flight, went wrong, or was selected belonged to the previous
// batch. The selection itself is reset by the ledger's own reload.
watch(
  () => app.data.batch.focusedId,
  () => {
    compute.reset()
    overflowFrom.value = 0
    selectionAtCapacity.value = false
  }
)
</script>

<template>
  <BaseTabbedPanel
    :breadcrumb="breadcrumb"
    :loading="ledger.pending"
    :error="ledger.error"
    :onRetry="() => ledger.load('retry')"
    :pt="
      app.ui.help.right(
        `
        <h1>Batch Peak Ledger</h1>
        <p>
        Cross-sample m/z anchors for the batch: each row is a species seen
        across samples, with its consensus formula and tier, the number of
        samples it appears in, and the highest intensity it reaches in any of
        them. Isotopologue peaks are folded under their main peak; the
        view-options button on the tier row unfolds them.
        </p>
        <p>
        The rows selected here are what the Assignments chart plots &mdash;
        Ctrl+A selects all filtered rows, up to the
        ${MAX_SELECTED_BATCH_PEAKS} the chart draws at once. Filter first to
        choose which ones, and <b>Compute batch peaks</b> in the bar above to
        rebuild them. Focus a sample to switch to its per-sample assignment
        ledger.
        </p>`,
        { doc: app.ui.help.docUrl('how-it-works/peak-assignment/#batch-peaks') }
      )
    "
  >
    <div class="col ledger" style="gap: 0.6rem; align-items: stretch">
      <!-- A launch that was refused or failed reports itself here rather than in
           a toast that has scrolled away by the time the user looks up. -->
      <Message
        v-if="compute.launchError"
        :severity="compute.launchRefused ? 'warn' : 'error'"
        closable
        @close="compute.launchError = null"
      >
        {{ compute.launchError }}
      </Message>

      <!-- Said here, where the gesture was made, rather than left for the user
           to infer from a chart that draws fewer traces than the ledger shows
           ticked. Informational rather than a warning: nothing went wrong, the
           selection just has a size. -->
      <Message v-if="selectionNotice" severity="secondary" icon="pi pi-exclamation-triangle">
        {{ selectionNotice }}
      </Message>

      <!-- An earlier run's ledger, from its snapshot. Said here so a formula
           that differs from what a sample's inspector shows reads as history
           rather than as a disagreement, and so the withheld verdict cells
           have their reason. -->
      <Message v-if="!runs.viewingCurrent" severity="info" icon="pi ph ph-clock-counter-clockwise">
        Showing the ledger as {{ viewedRunLabel }} left it, read-only. Verdicts and curation act on
        the current run; pick it in the run selector to judge.
      </Message>

      <div
        v-if="ledger.list.length"
        class="tier-strip"
        v-help.top="{
          title: 'Confidence Tiers',
          helpKey: 'assignment-tiers',
          doc: app.ui.help.docUrl('how-it-works/peak-assignment/#confidence-tiers')
        }"
      >
        <button
          v-for="chip in tierChips"
          :key="chip.key"
          type="button"
          class="tier-stat"
          :class="[
            chip.key,
            { active: activeTier === chip.key, dim: activeTier && activeTier !== chip.key }
          ]"
          v-tooltip.top="
            activeTier === chip.key ? `Showing only ${chip.label}` : `Filter to ${chip.label}`
          "
          @click="toggleTier(chip.key)"
        >
          <b>{{ chip.count }}</b> {{ chip.label }}
        </button>

        <!-- At the end of the filter row, because that is what it holds: the
             tier chips beside it narrow the table by confidence, the menu
             chooses whether isotopologues are their own rows. A cog, in the
             corner the sample ledger and the sample and ion browsers already
             put their table controls in. -->
        <Button
          class="view-menu-button"
          icon="pi pi-cog"
          severity="secondary"
          text
          size="small"
          aria-label="Ledger view options"
          aria-haspopup="dialog"
          :aria-controls="viewMenuOpen ? 'batch-peak-view-menu' : undefined"
          :aria-expanded="viewMenuOpen"
          v-tooltip.top="'View options: isotopologue rows'"
          @click="toggleViewMenu"
          :pt="
            app.ui.help.top(
              `
              <h1>View Options</h1>
              <p>
              How this ledger reads, rather than what it is reading.
              <b>Isotopologues</b> unfolds each species' isotopologue peaks -
              folded into the <b>+N</b> marker beside the formula by default -
              as indented rows under their main peak (M0). An isotopologue peak
              carries its family's formula, so left in the list it reads as a
              second species.
              </p>
              <p>
              The link is derived: a batch peak is an m/z anchor and carries no
              family of its own, so an isotopologue is one whose per-sample
              assignments agree, across the batch, that it belongs to another
              anchor's compound.
              </p>
              <p>
              The setting keeps while the menu is closed.
              </p>`,
              { doc: app.ui.help.docUrl('how-it-works/peak-assignment/#batch-peaks') }
            )
          "
        />
        <!-- Named, because Popover gives its panel role="dialog"
             aria-modal="true" and nothing else: an unnamed dialog is announced
             as just "dialog". Both attributes land on that root - Popover
             merges fallthrough attrs into it via ptmi. -->
        <Popover
          ref="viewMenu"
          id="batch-peak-view-menu"
          aria-label="Ledger view options"
          @show="viewMenuOpen = true"
          @hide="viewMenuOpen = false"
        >
          <div class="view-menu">
            <div
              class="unfold-toggle"
              v-tooltip.top="'Show isotopologue peaks as indented rows under their main peak'"
            >
              <!-- autofocus on the switch itself, not on its wrapper: Popover
                   moves focus only to a genuinely focusable `[autofocus]` child,
                   and ToggleSwitch puts a fallthrough attribute on its root div.
                   Without it the panel opens with nothing focused and, being
                   teleported to the end of <body>, is unreachable by keyboard. -->
              <ToggleSwitch
                v-model="showIsotopologues"
                inputId="unfold-batch-iso"
                :pt="{ input: { autofocus: true } }"
              />
              <label for="unfold-batch-iso">Isotopologues</label>
            </div>
            <!-- The whole ledger as a CSV: every batch peak with every
                 sample's member, one row per member. A background task; the
                 browser downloads the file when it is ready. -->
            <Button
              class="export-ledger"
              label="Export ledger (CSV)"
              icon="pi ph ph-download-simple"
              size="small"
              text
              severity="secondary"
              :disabled="!parents.length || compute.exporting"
              :loading="compute.exporting"
              v-tooltip.top="
                'Download the whole ledger as CSV: one row per member peak, with the batch peak it folded into'
              "
              @click="compute.exportLedger()"
            />
          </div>
        </Popover>
      </div>

      <div class="overlay" style="text-align: center" v-if="ledger.pending">
        <ProgressSpinner />
      </div>

      <DataTable
        v-else
        :value="rows"
        dataKey="batch_peak_id"
        :selection="ledger.selected"
        @update:selection="applySelection"
        :selectAll="allSelected"
        @select-all-change="onSelectAllChange"
        v-model:filters="filters"
        selectionMode="multiple"
        :metaKeySelection="false"
        @keydown="onKeyDown"
        filterDisplay="menu"
        resizableColumns
        size="small"
        scrollable
        scrollHeight="flex"
        :virtualScrollerOptions="{ itemSize: 35.74 }"
        lazy
        removableSort
        v-model:sortField="sortField"
        v-model:sortOrder="sortOrder"
        :pt="
          app.ui.help.top(
            { title: 'Batch Peaks', helpKey: 'batch-peaks' },
            { doc: app.ui.help.docUrl('how-it-works/peak-assignment/#batch-peaks') }
          )
        "
      >
        <template #empty> No batch peaks yet - run "Rebuild batch ledger" to populate. </template>

        <Column selectionMode="multiple" style="width: 3rem" />

        <Column field="mz" header="m/z" sortable style="min-width: 7rem">
          <template #body="{ data }">{{ num.mz.format(data.mz) }}</template>
        </Column>

        <!-- One number per species out of a per-sample matrix, so the column
             says which one it picked: the brightest sample, which is where a
             species is best measured and the order a reader looks for the
             largest thing in the batch in. -->
        <Column
          field="max_intensity"
          header="Intensity"
          sortable
          style="min-width: 7rem"
          v-tooltip="intensityTooltip"
        >
          <template #body="{ data }">
            <span class="intensity">
              {{ data.max_intensity != null ? num.peakIntensity.format(data.max_intensity) : '—' }}
            </span>
            <!-- The chart's click-through, from the row: with several traces
                 plotted it is not obvious which point to click, and the
                 brightest sample is where the species is best measured. -->
            <button
              type="button"
              class="jump-cell"
              :disabled="jump.pendingId != null || !data.n_present"
              aria-label="Open the brightest sample with this peak in focus"
              v-tooltip.top="'Open the brightest sample with this peak in focus (Sample tab)'"
              @click.stop="jump.jumpToBrightest(data)"
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
        </Column>

        <Column field="consensus_formula" header="Formula" sortable style="min-width: 9rem">
          <template #body="{ data }">
            <!-- An unfolded isotopologue says what it is rather than repeating its
                 family's formula, which is the whole reason it was folded. -->
            <span v-if="data.parentId" class="child-cell">
              <span class="child-caret">&#8627;</span>
              <span class="child-label" v-tooltip.top="data.consensus_formula || 'isotopologue'">{{
                childLabel(data)
              }}</span>
            </span>
            <!-- The isotopologue count rides in the slot, outside what gets
                 copied, as the sample ledger's does. -->
            <BaseCopyableField
              v-else-if="data.consensus_formula"
              class="formula"
              :field="data.consensus_formula"
            >
              <span
                v-if="data.curated"
                class="pi ph ph-hand-pointing curated"
                v-tooltip.top="'Curated by hand for the whole batch'"
              />
              <span
                v-if="isotopologueCount(data)"
                class="iso-count"
                v-tooltip.top="
                  `${isotopologueCount(data)} isotopologue peak${
                    isotopologueCount(data) === 1 ? '' : 's'
                  }`
                "
                >+{{ isotopologueCount(data) }}</span
              >
            </BaseCopyableField>
            <span v-else class="unassigned">unassigned</span>
          </template>
          <template #filter="{ filterModel, filterCallback }">
            <InputText
              v-model="filterModel.value"
              @input="filterCallback()"
              placeholder="Search formula..."
              size="small"
            />
          </template>
        </Column>

        <!-- `field` stays the tier so the filter menu keeps its constraint;
             `sortField` is what the header click sorts on, which is how the
             column orders by confidence without the filter losing its binding.

             One constraint, and no button to add a second: the menu defaults to
             offering "Add Rule" up to two rules, and a second rule is one the
             chips above can neither show nor clear - two EQUALS tiers ANDed
             together also match nothing, so the table would empty out while a
             chip still claimed to be showing a tier. -->
        <Column
          field="consensus_tier"
          sortField="tierRank"
          sortable
          style="min-width: 8rem"
          :filterMatchModeOptions="[{ label: 'Equals', value: 'equals' }]"
          :maxConstraints="1"
          :showAddButton="false"
          :showOperator="false"
        >
          <template #header>
            <span
              v-help.top="{
                title: 'Confidence Tiers',
                helpKey: 'assignment-tiers',
                doc: app.ui.help.docUrl('how-it-works/peak-assignment/#confidence-tiers')
              }"
              >Tier</span
            >
          </template>
          <template #body="{ data }">
            <!-- No number beside this one. A batch peak's consensus tier is a
                 weighted vote over its members' tiers, not a threshold on any
                 single quantity, so there is no evidence figure that produced it.
                 `best_fit_score` is the best member's fit and is still served and
                 sorted on; showing it here would read as the number the tier came
                 from, which it never was. -->
            <BaseTierTag :tier="data.consensus_tier" />
          </template>
          <template #filter="{ filterModel, filterCallback }">
            <Select
              v-model="filterModel.value"
              @change="filterCallback()"
              :options="TIERS"
              placeholder="Any tier"
              size="small"
              :showClear="true"
            />
          </template>
        </Column>

        <Column
          field="n_present"
          header="Samples"
          sortable
          style="min-width: 6rem"
          v-tooltip="'Number of samples this species is seen in'"
        >
          <template #body="{ data }">{{ data.n_present }}</template>
        </Column>

        <!-- Batch-level verdict: one judgment per species at this batch peak,
             covering every sample in the batch without a verdict of its own. The
             cell is a button whichever state it is in, so an unjudged row is
             judged from the same place a judged one is re-judged. Sorts through
             `compareBy` on the rank the projection carries - confirmed first,
             unjudged last - as the tier column does. -->
        <Column field="verdictRank" sortable style="min-width: 4rem">
          <template #header>
            <span
              class="pi ph ph-seal-check"
              v-tooltip.top="'Batch-level verdict'"
              v-help.top="{
                title: 'Batch-Level Verdicts',
                helpKey: 'batch-peak-verdicts',
                doc: app.ui.help.docUrl('how-it-works/peak-assignment/#batch-level-verdicts')
              }"
            />
          </template>
          <template #body="{ data }">
            <button
              type="button"
              class="verdict-cell"
              :class="{ stale: verdictStale(data), empty: !verdictFor(data) }"
              :disabled="!runs.viewingCurrent"
              :aria-label="
                verdictFor(data) ? 'Batch-level verdict' : 'Record a batch-level verdict'
              "
              v-tooltip.top="verdictTooltip(data)"
              @click.stop="openVerdict($event, data)"
            >
              <BaseVerdictBadge v-if="verdictFor(data)" :record="verdictFor(data)" compact />
              <span v-else class="pi ph ph-seal-check" />
            </button>
          </template>
        </Column>
      </DataTable>

      <Popover ref="verdictMenu" aria-label="Batch-level verdict">
        <BatchPeakVerdictPopover
          v-if="verdictTarget"
          :row="verdictTarget"
          :record="verdictFor(verdictTarget)"
          :stale="verdictStale(verdictTarget)"
          @done="verdictMenu?.hide()"
        />
      </Popover>
    </div>
  </BaseTabbedPanel>
</template>

<style scoped>
/* The verdict cell is a button so an empty cell opens the popover too; the badge
   - or the faint seal for "none yet" - is its whole content. Stale: the judgment
   is about a formula the consensus has since left, outlined in the warning
   colour until re-judged or retracted. */
.verdict-cell {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0.1rem 0.3rem;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.verdict-cell.empty .pi {
  opacity: 0.25;
}
/* History is read-only: the cell keeps its badge and loses its hand. */
.verdict-cell:disabled {
  cursor: default;
  opacity: 0.6;
}
.verdict-cell:hover {
  background: var(--p-content-hover-background);
}
.verdict-cell.stale {
  border-color: var(--state-warning);
  border-style: dashed;
}

.unassigned {
  color: var(--p-text-muted-color, #888);
  font-style: italic;
}

.intensity {
  font-variant-numeric: tabular-nums;
}

/* The view menu, its toggle and the child-row treatment are the sample ledger's
   (PaneBrowserAssignment.vue): the two ledgers sit in the same tab position and
   fold the same thing, so a reader switching between them should find the same
   control in the same corner, and recognize the same indent under it. */
.view-menu-button {
  margin-left: auto;
  align-self: center;
}

/* The view-options panel: one labelled setting per row. */
.view-menu {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  min-width: 14rem;
}
.unfold-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
  font-size: 0.8rem;
  white-space: nowrap;
  /* Label first, control last, as in the sample ledger's menu. Reversed here
     rather than reordered in the markup so the switch stays the first element
     in the panel, and so it is what the panel's autofocus lands on. */
  flex-direction: row-reverse;
}
.unfold-toggle label {
  cursor: pointer;
  opacity: 0.75;
}

/* Unfolded isotopologue row: indented offset label under its main peak. */
.child-cell {
  display: inline-flex;
  align-items: baseline;
  gap: 0.35rem;
  padding-left: 0.9rem;
}
.child-caret {
  opacity: 0.4;
}
.child-label {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.86rem;
  opacity: 0.8;
}
.curated {
  font-size: 0.8rem;
  margin-right: 0.25rem;
  opacity: 0.8;
}
.iso-count {
  margin-left: 0.35rem;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.62rem;
  opacity: 0.55;
  vertical-align: super;
  /* Its parent is BaseCopyableField's flex row, where vertical-align does
     nothing; align to the top edge to keep the superscript reading. */
  align-self: flex-start;
}
/* The copy button sits between the formula and the marker in source order, and
   it reserves its space even while hidden - which would strand the "+N" a
   button's width away from the formula it counts for. Ordering it last in the
   flex row puts the marker back against the formula. */
.formula :deep(button) {
  order: 1;
}

/* Matches the sample ledger's strip (PaneBrowserAssignment.vue): the two sit in
   the same tab position, and a reader switching between a sample and its batch
   should recognize the same control. */
.tier-strip {
  display: flex;
  flex-flow: row wrap;
  gap: 0.3rem;
  padding: 0 0.4rem;
}
.tier-stat {
  font-size: 0.72rem;
  padding: 0.15rem 0.5rem;
  border-radius: 100px;
  border: 1px solid var(--p-content-border-color, #e3e6ec);
  font-variant-numeric: tabular-nums;
  /* button reset */
  background: transparent;
  color: inherit;
  font-family: inherit;
  cursor: pointer;
  transition:
    opacity 0.12s,
    border-color 0.12s,
    background 0.12s;
}
.tier-stat:hover {
  border-color: var(--p-primary-color, #6366f1);
}
.tier-stat.active {
  border-color: var(--p-primary-color, #6366f1);
  background: color-mix(in srgb, var(--p-primary-color, #6366f1) 12%, transparent);
}
.tier-stat.dim {
  opacity: 0.4;
}
.tier-stat b {
  font-weight: 700;
}
.tier-stat.assigned b {
  color: var(--state-success);
}
.tier-stat.candidate b {
  color: var(--state-warning);
}
.tier-stat.below_assignability b,
.tier-stat.unassigned b {
  color: var(--p-surface-500, #6f7889);
}

/* The panel body is a column: the launch-error banner and the tier strip take
   their natural height and the ledger takes the rest, so whatever is shown
   above the table shortens it instead of pushing it past the pane (same
   pattern as PaneBrowserAssignment.vue). */
:deep(.p-panel-content) {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.ledger {
  flex: 1;
  min-height: 0;
  /* .col centres on space-between, which would drop the strip and the table
     apart now that this column can have room to spare. */
  justify-content: flex-start;
}
.ledger > :deep(.p-datatable) {
  flex: 1;
  min-height: 0;
}
.ledger > :deep(.p-datatable > .p-datatable-table-container) {
  flex: 1;
  min-height: 0;
}

/* The jump beside the intensity: faint until hovered, so the column stays a
   column of numbers; the spinner marks the one jump in flight. */
.jump-cell {
  margin-left: 0.35rem;
  padding: 0.05rem 0.25rem;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
  opacity: 0.45;
}
.jump-cell:hover {
  opacity: 1;
  background: var(--p-content-hover-background);
}
.jump-cell:disabled {
  cursor: default;
  opacity: 0.2;
}
</style>
