<script setup>
import { ref, reactive, computed, watch, onScopeDispose } from 'vue'

import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Message from 'primevue/message'
import Popover from 'primevue/popover'
import ProgressSpinner from 'primevue/progressspinner'
import ToggleSwitch from 'primevue/toggleswitch'

import { getApiErrorMessage, isRefusedRequest } from '@/api/utils'
import {
  BaseCopyableField,
  BaseLoadError,
  BaseTabbedPanel,
  BaseTierTag,
  BaseVerdictBadge
} from '@/lib/base'
import { PeakAssignConfigForm } from '@/lib/dialogs'
import { num } from '@/lib/formatters'
import { formatIsotopeFormula } from '@/lib/chem'
import { tierBucket, tierRank } from '@/lib/tiers'
import { prettyTrim } from '@/lib/utils'
import { useApp } from '@/stores'

import { useAssignmentLauncher } from './stores'

const app = useApp()
const launcher = useAssignmentLauncher()

const runs = computed(() => app.data.peakAssignment.run)
const assignments = computed(() => app.data.peakAssignment.peak)
const tierCounts = computed(() => assignments.value.tierCounts)

// Map ionization_mechanism_id -> readable notation (e.g. "+H+", "+Br-"), for
// the ledger's ionization column. The assignment carries only the id.
const mechById = computed(() => {
  const map = new Map()
  for (const mech of app.data.ionization.mechanism.list) {
    map.set(mech.ionization_mechanism_id, mech.ionization_mechanism)
  }
  return map
})

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
// below runs over parents only, so a family is one unit there already - keeping
// the two in step is the point.
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
// What the row visibly carries, its own verdict first. The filter runs on this,
// so "Unverified" never lists a row that shows a badge.
const effectiveVerdictFor = (row) => verdictFor(row) ?? overlayFor(row)

// Verdict filter (single-select). "unverified" = no current verdict.
const VERDICT_FILTERS = [
  { value: 'all', label: 'All verdicts' },
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'unsure', label: 'Unsure' },
  { value: 'unverified', label: 'Unverified' }
]
const verdictFilter = ref('all')

// --- View options menu ------------------------------------------------------

// The verdict filter above and the isotopologue fold below are the ledger's two
// view options, and they used to sit bare in the panel header beside the run
// selector and the launch button. Four controls never fitted a browser column
// the user can drag to half a window, so the two that only change how this one
// table reads went behind a cog at the end of the tier-chip row - everything
// that narrows the table on one row - and the two that outlive the table moved
// up into the switch bar.
//
// A Popover rather than a Menu: the isotopologue switch's only accessible name
// is its `<label for>`, which plain markup keeps and a menu's item roles would
// not. Both refs stay here, in the scope `rows` reads them from, so opening and
// closing the menu cannot lose a choice made in it.
const viewMenu = ref()
const viewMenuOpen = ref(false)
const toggleViewMenu = (event) => viewMenu.value?.toggle(event)

// --- Launch a run -----------------------------------------------------------

// The button that opens this dialog is a row up, in the switch bar, so the flag
// is shared through a store while everything the launch actually does stays
// here - next to the ledger a refusal is about. Exposed as a plain writable
// ref-alike so the dialog, the empty state and the watchers below read the same
// way they did when the flag was local.
//
// Cleared when this pane goes away, because the dialog goes with it: the flag
// is the only piece that now outlives the component, and a stale `true` would
// reopen the dialog on the next mount without the watcher below having run -
// so with last sample's configuration still in the form.
const configVisible = computed({
  get: () => launcher.configVisible,
  set: (value) => (launcher.configVisible = value)
})
onScopeDispose(() => {
  launcher.configVisible = false
})
const submitting = ref(false)
// A per-sample run is cheap and the user is looking at one spectrum, so the
// untargeted stage starts on here - unlike a batch, where cost scales with the
// number of samples. The rest is left unset for PeakAssignConfigForm to fill
// from the server defaults.
function initialConfig() {
  return {
    run_untargeted: true,
    mz_precision_ppm: null,
    formula_ranges: null,
    max_untargeted_peaks: null,
    peak_intensity_threshold: null,
    max_alternatives: null
  }
}

const config = reactive(initialConfig())

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

// Reset each time the dialog opens, the same way the batch launcher does: the
// form only fills fields that are still unset, so without this a value typed
// for one sample would silently carry into the next run. Also scope help mode
// to the dialog's cards while it is open (the config form registers its cards
// on this layer).
watch(configVisible, (open) => {
  if (open) {
    Object.assign(config, initialConfig())
    launchError.value = null
  }
  app.ui.help.set(open ? 'dialog_peak_assign' : null)
})

async function launch() {
  const sampleItemId = app.data.sample.focusedId
  if (!sampleItemId) return
  submitting.value = true
  try {
    // Drop anything still unset so the backend default applies rather than a
    // null overriding it.
    const payload = Object.fromEntries(
      Object.entries(config).filter(([, value]) => value !== null && value !== '')
    )
    await runs.value.assign(sampleItemId, payload)
    launchError.value = null
  } catch (error) {
    // A refusal is an answer, not a crash: the request was understood and
    // declined. Either way the dialog has nothing left to do - closing it puts
    // the reason in view rather than behind a modal the user has to dismiss to
    // read it.
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

// --- Tier ordering & filtering ----------------------------------------------

// Confidence order (assigned first, unassigned last) comes from the shared
// tier module: it drives the default sort here and the same one in the
// batch-peaks pane, which is the point of sharing it - two ledgers side by side
// that ranked tiers differently would be worse than either being wrong alone.

// Histogram bucket for a row: reagent/artifact roles are their own bucket,
// matching the counts strip and the spectrum coloring. Tier ranking itself
// lives in @/lib/tiers so this ledger and the batch-peak ledger cannot drift.
function bucketOf(row) {
  if (row.role === 'reagent' || row.role === 'artifact') return 'reagent'
  return tierBucket(row.tier)
}

// Active tier filters (empty = show all); clicking a histogram chip toggles it.
const activeTiers = reactive(new Set())
function toggleTier(key) {
  if (activeTiers.has(key)) activeTiers.delete(key)
  else activeTiers.add(key)
}

// Fold isotopologues by default: one row per assigned formula (M0)
// plus unassigned/reagent peaks, which keeps this a flat, fixed-height list
// compatible with virtual scrolling. Toggle to unfold (see below).
const showIsotopologues = ref(false)

// --- Sorting ----------------------------------------------------------------

// The sort is the pane's, not the table's. PrimeVue sorts the flat row array it
// is handed, which tears every isotopologue away from the parent it
// belongs under the moment the user sorts by anything but the default - sorting
// by intensity drops a child hundreds of rows below its "+N" parent, where an
// indented "iso" label means nothing. `lazy` hands sorting back to us so `rows`
// can order the parents and re-attach each family underneath.
//
// `lazy` also switches off DataTable's own filtering and paging: neither is in
// use here (the tier chips and the verdict filter are applied in `rows`, and
// the table virtual-scrolls rather than paginates), so nothing else changes.
// The header still renders its sort indicator and still emits the field it was
// clicked with; `removableSort` gives a third click that clears the column and
// returns the ledger to its confidence-ordered default.
const sortField = ref('tierRank')
const sortOrder = ref(1)

// Numeric collation, so the formula column reads as a chemist expects: C2H6
// before C10H22, not after it. This is the comparer PrimeVue sorted with -
// @primeuix/utils' localeComparator() is
// `new Intl.Collator(undefined, { numeric: true }).compare` - and taking the
// sort over must not quietly change the order it used to produce.
const collator = new Intl.Collator(undefined, { numeric: true })

// Compare two rows on one column. Missing values sort last in both directions -
// a peak with no formula is unknown, not "smallest" - counting the empty string
// as missing, which is what PrimeVue's isEmpty() did.
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
const byConfidence = (a, b) => a.tierRank - b.tierRank || (b.fit_score ?? -1) - (a.fit_score ?? -1)

// Table rows. Parents (M0 + unassigned/reagent) are filtered by the active
// chips, then ordered by the sorted column with confidence breaking ties. When
// unfolded, each parent's iso_child isotopologues are inserted right after it,
// ordered by m/z among themselves - a family is one block wherever its parent
// lands, which is the only arrangement in which the indented child rows can be
// read at all.
const rows = computed(() => {
  const parents = assignments.value.list
    .filter((row) => row.role !== 'iso_child')
    .filter((row) => activeTiers.size === 0 || activeTiers.has(bucketOf(row)))
    .filter(
      (row) =>
        verdictFilter.value === 'all' ||
        (effectiveVerdictFor(row)?.verdict ?? 'unverified') === verdictFilter.value
    )
    .map((row) => ({
      ...row,
      tierRank: tierRank(row.tier),
      // Null where the producing engine stated no tier, and null rather than a
      // rank so `compareBy` sorts those rows last in both directions - "this
      // engine said nothing" is not a position on the scale. Guarded because
      // tierRank(null) would answer with the 'unassigned' rank and put every
      // in-app row at one end of a column it has no opinion in.
      engineTierRank: row.engine_tier != null ? tierRank(row.engine_tier) : null,
      // The calibrated probability for the sortable P(correct) column; null for
      // untargeted / uncalibrated (rendered as "-", never 0%). The ledger rows
      // carry it flattened (`p_correct`); the `provenance` fallback covers rows
      // from a backend that predates the slim ledger projection.
      pCorrect: row.p_correct ?? row.provenance?.p_correct ?? null,
      pProvisional: row.p_correct_provisional ?? row.provenance?.calibration?.provisional ?? false,
      corrobAdducts: row.corroboration_adducts ?? row.provenance?.corroboration?.n_adducts ?? 0,
      corrobInherited: false,
      mech: mechById.value.get(row.ionization_mechanism_id) ?? null,
      isChild: false
    }))
  const byColumn = sortField.value ? compareBy(sortField.value, sortOrder.value) : null
  parents.sort(byColumn ? (a, b) => byColumn(a, b) || byConfidence(a, b) : byConfidence)
  if (!showIsotopologues.value) return parents

  const result = []
  for (const parent of parents) {
    result.push(parent)
    const children = assignments.value
      .childrenOf(parent.peak_assignment_id)
      .slice()
      .sort((a, b) => (a.sample_peak_mz ?? 0) - (b.sample_peak_mz ?? 0))
      .map((child) => {
        // Adduct corroboration is written onto the M0 winner alone: an isotopologue
        // is the same ion measured at another isotope, not a second sighting of
        // the compound, so it never carries a count of its own. The evidence is
        // about the formula the family shares, so the isotopologue shows its
        // parent's count and the marker says where it came from.
        const own = child.corroboration_adducts ?? child.provenance?.corroboration?.n_adducts
        return {
          ...child,
          tierRank: parent.tierRank,
          // No `engineTierRank` here on purpose. Only the parents are sorted -
          // children are spliced in under whichever parent they belong to,
          // whatever the sort - so a rank on a child would never be read, and
          // one derived from the child's OWN tier would contradict the line
          // above it, which exists precisely so a family sorts as one block.
          // The chip in the column body renders `engine_tier` directly.
          pCorrect: child.p_correct ?? child.provenance?.p_correct ?? null,
          pProvisional:
            child.p_correct_provisional ?? child.provenance?.calibration?.provisional ?? false,
          corrobAdducts: own ?? parent.corrobAdducts,
          // True whenever the count on this row is the parent's, independent of
          // whether it clears the marker's threshold, so the row stays
          // self-describing to anything that reads it below that threshold.
          corrobInherited: own == null && parent.corrobAdducts > 0,
          mech: mechById.value.get(child.ionization_mechanism_id) ?? null,
          isChild: true
        }
      })
    result.push(...children)
  }
  return result
})

// Label for an unfolded isotopologue child row (compact substitution label,
// falling back to the offset label).
const childLabel = (row) =>
  row.isotope_formula ? formatIsotopeFormula(row.isotope_formula) : row.isotope_label || 'iso'

// Calibrated probability formatter for the P(correct) column.
const pctFmt = new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 0 })

// A borrowed count is parenthesised, so an isotopologue does not read at a glance as
// a peak seen through several adducts in its own right.
const corrobLabel = (row) =>
  row.corrobInherited ? `(${row.corrobAdducts})` : `${row.corrobAdducts}`

// Why a row shows no calibrated probability. Several different reasons, and the
// wrong one is worse than none: a hand-assigned row has no P(correct) because
// nobody calibrated the formula a person chose, which says nothing about
// whether this instrument has a curve - and reading "no calibration curve for
// this instrument" there would send someone to calibrate an instrument that is
// calibrated perfectly well.
//
// Kept as one list because the column header has to name all of them: a reader
// deciding whether the column is worth sorting on cannot hover every dash in it
// to find out why the dashes are there. Written once so a reworded reason - or
// a fifth one - cannot land in the cells and miss the header.
const UNCALIBRATED_REASONS = {
  // First because a demoted satellite is both: curation.py's _demote strips the
  // satellites of a formula their M0 no longer holds and leaves source =
  // 'manual' on them, so a row can be a person's doing and hold no formula at
  // all. "Assigned by hand" on a row the tier chip beside it labels Unassigned
  // is the ledger's copy contradicting itself.
  unassigned: 'Nothing assigned to this peak',
  manual: 'Assigned by hand - the calibration never scored this formula',
  untargeted: 'Untargeted assignment - no calibrated probability',
  uncalibrated: 'No calibration curve for this instrument'
}

const uncalibratedReason = (row) => {
  if (!row.assigned_formula) {
    return UNCALIBRATED_REASONS.unassigned
  }
  if (row.source === 'manual') {
    return UNCALIBRATED_REASONS.manual
  }
  if (row.source === 'untargeted') {
    return UNCALIBRATED_REASONS.untargeted
  }
  return UNCALIBRATED_REASONS.uncalibrated
}

// The column header tells the same story the empty cells tell, assembled from
// the same list rather than summarised again in its own words - the old header
// promised only "untargeted / uncalibrated show -", which is now two reasons
// out of four and left a reader with no account of the other two.
const pCorrectHeaderTooltip =
  'Calibrated probability the assignment is correct, from database-stage ' +
  'assignments on calibrated instruments. A cell reads a dash when there is ' +
  `none: ${Object.values(UNCALIBRATED_REASONS).join('; ')}.`

// Tooltip for the adduct-corroboration marker. An isotopologue shows the count its
// M0 was corroborated by, so it has to say both that the evidence is the
// family's and that the boost is in the M0's P(correct) - the engine folds it
// into the record carrying the corroboration and never into a child's, so
// the number this marker sits beside does not include it.
const corrobTooltip = (row) =>
  row.corrobInherited
    ? `Supported by ${row.corrobAdducts} adducts, via the M0 of this isotopologue family ` +
      "(folded into the M0's P(correct), not into this row's)"
    : `Supported by ${row.corrobAdducts} adducts (already folded into P(correct))`

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
    // Folded: a focused isotopologue child maps to its M0 row.
    const assignment = assignments.value.forPeak(focused.peak_id)
    const ownerId = assignment?.role === 'iso_child' ? assignment.owner_peak_assignment_id : null
    return ownerId != null
      ? (rows.value.find((r) => r.peak_assignment_id === ownerId) ?? null)
      : null
  },
  set: (row) => {
    // Clicking the selected row again de-selects it, and PrimeVue says so by
    // emitting null. Dropping that left the peak focused with nothing selected:
    // the spectrum highlight, the inspector and the timeseries all stayed on a
    // peak the ledger no longer showed as chosen. Selection lives in the peak
    // store, so unfocusing there clears every one of them.
    if (row) focusPeak(row)
    else app.data.peak.unfocus()
  }
})

// Whether this run carries a tier of its producing engine's own. Only an
// imported run does - an in-app row's engine tier IS its `tier` - so the column
// is absent from every in-app ledger rather than sitting there empty. Read off
// the loaded rows rather than the run's engine, because "peaky published this"
// and "peaky stated a tier on these rows" are different claims: an engine may
// publish without tiering anything.
const hasEngineTiers = computed(() => assignments.value.list.some((row) => row.engine_tier != null))

// Says which of the two tiers this chip is, and whether it agrees. Without it
// two chips on one row read as one claim rendered twice.
const engineTierTooltip = (row) =>
  row.engine_tier === row.tier
    ? `The producing engine's own tier: ${row.engine_tier} (agrees with this server's)`
    : `The producing engine's own tier: ${row.engine_tier}, where this server's ` +
      `banding of the evidence says ${row.tier}`

// Isotopologues folded under a formula's M0.
const isoCount = (row) => assignments.value.childrenOf(row.peak_assignment_id).length

// --- Header ------------------------------------------------------------------

// Which sample's ledger this is, and the way back out of it. A pane headed only
// "Assignments" names neither the sample nor its batch, and until now the sole
// ways to unfocus the sample were a meta-click on its row in the sample browser
// or the filter chip in the far corner - both a long way from the table being
// read. The leading caret drops the sample, which returns the browser to the
// batch-peak ledger (PaneBrowserMatch switches on sample.focused) and clears the
// sample everywhere else.
const breadcrumb = computed(() => {
  const sample = app.data.sample.focused
  if (!sample) return null
  const batch = app.data.batch.focused
  return {
    items: [
      ...(batch
        ? [
            {
              icon: 'pi pi-hashtag',
              label: prettyTrim(batch.sample_batch_name, 45),
              disabled: false,
              tooltip: `Batch: ${batch.sample_batch_name}`,
              action: () => app.data.sample.unfocus()
            }
          ]
        : []),
      {
        icon: 'pi pi-tag',
        label: prettyTrim(sample.sample_item_name, 25),
        disabled: true,
        tooltip: `Peak assignments for sample:\n ${sample.sample_item_name}`
      },
      {
        icon: 'pi ph ph-atom',
        label: `${assignments.value.list.length} peaks`,
        disabled: true
      }
    ]
  }
})
</script>

<template>
  <BaseTabbedPanel
    label="Assignments"
    icon="pi ph ph-list-magnifying-glass"
    :breadcrumb="breadcrumb"
    :pt="
      app.ui.help.right(
        `
        <h1>Assignments</h1>
        <p>
        Every peak in the selected sample with its committed assignment from the
        selected run: formula, ionization, confidence tier and calibrated
        P(correct).
        </p>
        <p>
        Click a row to focus the peak in the spectrum and inspector. Use the
        tier chips to filter by confidence, the view-options button for
        isotopologue rows and the verdict filter, and <b>Assign peaks</b> in the
        bar above to launch a new run.
        </p>`,
        { doc: app.ui.help.docUrl('how-it-works/peak-assignment/') }
      )
    "
  >
    <Message
      v-if="launchError"
      :severity="launchRefused ? 'warn' : 'error'"
      closable
      @close="launchError = null"
    >
      {{ launchError }}
    </Message>

    <div v-if="!app.data.sample.focused" class="center empty">
      <div class="col" style="gap: 0.5rem; text-align: center; max-width: 40ch">
        <strong><span class="pi ph ph-hand-pointing" /> No sample selected</strong>
        <i style="opacity: 0.6"> Select a sample to view or run its peak assignments. </i>
      </div>
    </div>
    <!-- Ahead of the empty state: a run list that failed to load must not read
         as "this sample has none", which invites an Assign run the user does
         not need. -->
    <div v-else-if="runs.error" class="center empty">
      <BaseLoadError
        :error="runs.error"
        fallback="Could not load the assignment runs for this sample."
        :onRetry="() => runs.load('retry')"
      />
    </div>
    <div class="overlay" style="text-align: center" v-else-if="runs.pending">
      <ProgressSpinner />
    </div>
    <!-- The one state that carries its own call to action, and the reason
         AssignmentRunBar hides its copy of the button here: two identical
         buttons a few centimetres apart read as two different things. The four
         run states are a set maintained in two files - no sample, run list
         errored, no runs, and a run to show - so a change to any of these
         branches needs the bar's `canAssign` read alongside it. Both buttons
         open the same dialog, below, through the launcher store. -->
    <div v-else-if="!runs.list.length" class="center empty">
      <div class="col" style="gap: 0.75rem; text-align: center; max-width: 40ch">
        <strong><span class="pi ph ph-info" /> No assignment runs</strong>
        <i style="opacity: 0.6">
          Assign a composition to every peak in this sample: first from the known target library,
          then via untargeted composition search.
        </i>
        <Button
          label="Assign peaks"
          icon="pi ph ph-magic-wand"
          size="small"
          @click="configVisible = true"
        />
      </div>
    </div>

    <div v-else class="col ledger" style="gap: 0.6rem; align-items: stretch">
      <div
        class="tier-strip"
        v-help.top="{
          title: 'Confidence Tiers',
          helpKey: 'assignment-tiers',
          doc: app.ui.help.docUrl('how-it-works/peak-assignment/#confidence-tiers')
        }"
      >
        <button
          v-for="t in [
            { key: 'assigned', label: 'assigned', count: tierCounts.assigned },
            { key: 'candidate', label: 'candidate', count: tierCounts.candidate },
            { key: 'reagent', label: 'reagent', count: tierCounts.reagent },
            { key: 'below_assignability', label: 'below', count: tierCounts.below_assignability },
            { key: 'unassigned', label: 'unassigned', count: tierCounts.unassigned }
          ]"
          :key="t.key"
          type="button"
          class="tier-stat"
          :class="[
            t.key === 'below_assignability' ? 'below' : t.key,
            {
              active: activeTiers.has(t.key),
              dim: activeTiers.size && !activeTiers.has(t.key)
            }
          ]"
          v-tooltip.top="
            activeTiers.has(t.key) ? `Showing only ${t.label}` : `Filter to ${t.label}`
          "
          @click="toggleTier(t.key)"
        >
          <b>{{ t.count }}</b> {{ t.label }}
        </button>

        <!-- At the end of the filter row, because that is what it holds: the
             tier chips beside it narrow the table by confidence, the menu
             narrows it by verdict and chooses whether isotopologues are their
             own rows. A cog, matching the table-controls button the sample and
             ion browsers already put in the same corner. -->
        <Button
          class="view-menu-button"
          icon="pi pi-cog"
          severity="secondary"
          text
          size="small"
          aria-label="Ledger view options"
          aria-haspopup="dialog"
          :aria-controls="viewMenuOpen ? 'assignment-view-menu' : undefined"
          :aria-expanded="viewMenuOpen"
          v-tooltip.top="'View options: isotopologue rows, verdict filter'"
          @click="toggleViewMenu"
          :pt="
            app.ui.help.top(
              `
              <h1>View Options</h1>
              <p>
              How this ledger reads, rather than what it is reading. <b>Isotopologues</b>
              unfolds each compound's isotopologue peaks - folded into the <b>+N</b>
              marker by default - as indented rows under their main peak (M0).
              <b>Verdict</b> narrows the table to one verification verdict.
              </p>
              <p>
              Both keep their setting while the menu is closed.
              </p>`,
              { doc: app.ui.help.docUrl('how-it-works/peak-assignment/') }
            )
          "
        />
        <!-- Named, because Popover gives its panel role="dialog"
             aria-modal="true" and nothing else: an unnamed dialog is announced
             as just "dialog". Both attributes land on that root - Popover
             merges fallthrough attrs into it via ptmi. -->
        <Popover
          ref="viewMenu"
          id="assignment-view-menu"
          aria-label="Ledger view options"
          @show="viewMenuOpen = true"
          @hide="viewMenuOpen = false"
        >
          <div class="view-menu">
            <div
              class="unfold-toggle"
              v-tooltip.top="'Show isotopologue peaks as indented rows under their compound'"
            >
              <!-- autofocus on the switch itself, not on its wrapper: Popover
                   moves focus only to a genuinely focusable `[autofocus]` child,
                   and ToggleSwitch puts a fallthrough attribute on its root div.
                   Without it the panel opens with nothing focused and, being
                   teleported to the end of <body>, is unreachable by keyboard. -->
              <ToggleSwitch
                v-model="showIsotopologues"
                inputId="unfold-iso"
                :pt="{ input: { autofocus: true } }"
              />
              <label for="unfold-iso">Isotopologues</label>
            </div>
            <!-- Chips rather than a dropdown, and the same shape as the tier
                 chips this menu hangs off. A Select here would be a second
                 overlay inside a focus-trapping one: PrimeVue's Select swallows
                 Escape unconditionally (stopPropagation in its onEscapeKey) and
                 both of Popover's Escape handlers listen on the bubble path, so
                 a keyboard user who tabbed into the filter could neither close
                 the menu with Escape nor tab out past the trap's sentinels.
                 Plain buttons keep Escape working and name themselves. -->
            <div class="verdict-filter" role="group" aria-label="Filter by verification verdict">
              <span class="view-menu-label">Verdict</span>
              <div class="verdict-chips">
                <button
                  v-for="option in VERDICT_FILTERS"
                  :key="option.value"
                  type="button"
                  class="verdict-chip"
                  :class="{ active: verdictFilter === option.value }"
                  :aria-pressed="verdictFilter === option.value"
                  @click="verdictFilter = option.value"
                >
                  {{ option.label }}
                </button>
              </div>
            </div>
          </div>
        </Popover>
      </div>

      <div v-if="assignments.pending" class="center loading-region">
        <ProgressSpinner />
      </div>
      <!-- Inline rather than through the panel's :error, so the tier strip and
           its view menu above stay usable, and the run selector in the bar
           above them keeps offering another run, while only this run's rows are
           unavailable. -->
      <div v-else-if="assignments.error" class="center loading-region">
        <BaseLoadError
          :error="assignments.error"
          fallback="Could not load this run."
          :onRetry="() => assignments.load('retry')"
        />
      </div>
      <DataTable
        v-else
        :value="rows"
        dataKey="sample_peak_id"
        size="small"
        scrollable
        scrollHeight="flex"
        :virtualScrollerOptions="{ itemSize: 35.5 }"
        lazy
        removableSort
        v-model:sortField="sortField"
        v-model:sortOrder="sortOrder"
        selectionMode="single"
        :metaKeySelection="false"
        v-model:selection="selectedRow"
      >
        <Column field="sample_peak_mz" header="m/z" sortable style="min-width: 6rem">
          <template #body="{ data }">{{ num.mz.format(data.sample_peak_mz) }}</template>
        </Column>
        <Column field="sample_peak_intensity" header="intensity" sortable style="min-width: 5rem">
          <template #body="{ data }">
            {{
              data.sample_peak_intensity != null
                ? num.peakIntensity.format(data.sample_peak_intensity)
                : '—'
            }}
          </template>
        </Column>
        <Column field="assigned_formula" header="formula" sortable style="min-width: 6rem">
          <template #body="{ data }">
            <span v-if="data.isChild" class="child-cell">
              <span class="child-caret">&#8627;</span>
              <span
                class="child-label"
                v-tooltip.top="data.isotope_formula || data.isotope_label"
                >{{ childLabel(data) }}</span
              >
            </span>
            <!-- The formula is the one cell in the ledger a reader wants out of
                 the app - into a search, a note, a target list - so it carries
                 the same hover-to-copy affordance as the batch ledger's. The
                 isotopologue count rides in the slot, outside what gets copied. -->
            <BaseCopyableField
              v-else-if="data.assigned_formula"
              class="formula"
              :field="data.assigned_formula"
            >
              <span
                v-if="isoCount(data)"
                class="iso-count"
                v-tooltip.top="
                  `${isoCount(data)} isotopologue peak${isoCount(data) === 1 ? '' : 's'}`
                "
                >+{{ isoCount(data) }}</span
              >
            </BaseCopyableField>
            <span v-else class="formula">&mdash;</span>
          </template>
        </Column>
        <Column field="mech" sortable style="min-width: 5rem">
          <template #header>
            <span v-tooltip.top="'Ionization mechanism (adduct)'">ionization</span>
          </template>
          <template #body="{ data }">
            <span class="mech">{{ data.mech || '—' }}</span>
          </template>
        </Column>
        <Column field="tierRank" header="tier" sortable style="min-width: 7rem">
          <template #body="{ data }">
            <BaseTierTag
              :tier="data.tier"
              :evidence="data.evidence"
              :role="data.role"
              :source="data.source"
            />
          </template>
        </Column>
        <!-- The producing engine's own verdict, beside this server's rather
             than instead of it. Both are real and they answer different
             questions: `tier` is the evidence banded against the run's declared
             thresholds - and is what this table sorts, filters and rolls up on -
             while this one is what the engine concluded on its own terms. A row
             where they differ is the one worth opening. -->
        <Column
          v-if="hasEngineTiers"
          field="engineTierRank"
          header="engine tier"
          sortable
          style="min-width: 7rem"
        >
          <template #body="{ data }">
            <BaseTierTag
              v-if="data.engine_tier"
              :tier="data.engine_tier"
              :show-evidence="false"
              :tooltip="engineTierTooltip(data)"
            />
            <span v-else class="no-engine-tier">&mdash;</span>
          </template>
        </Column>
        <Column field="pCorrect" sortable style="min-width: 6.5rem">
          <template #header>
            <span
              v-tooltip.top="pCorrectHeaderTooltip"
              v-help.top="{
                title: 'P(correct)',
                helpKey: 'assignment-p-correct',
                doc: app.ui.help.docUrl(
                  'how-it-works/peak-assignment/#calibrated-confidence-probability-of-being-correct'
                )
              }"
              >P(correct)</span
            >
          </template>
          <template #body="{ data }">
            <span v-if="data.pCorrect != null" class="pcorrect">
              {{ pctFmt.format(data.pCorrect)
              }}<span
                v-if="data.pProvisional"
                class="prov"
                v-tooltip.top="'Provisional calibration curve'"
                >*</span
              >
            </span>
            <span v-else class="pcorrect uncal" v-tooltip.top="uncalibratedReason(data)"
              >&mdash;</span
            >
            <span
              v-if="data.corrobAdducts > 1"
              class="corrob-mark"
              v-tooltip.top="corrobTooltip(data)"
              ><span class="pi ph ph-link-simple" />{{ corrobLabel(data) }}</span
            >
          </template>
        </Column>
        <Column style="min-width: 3rem">
          <template #header>
            <span
              class="pi ph ph-seal-check"
              v-tooltip.top="'Verification verdict'"
              v-help.top="{
                title: 'Verification',
                helpKey: 'assignment-verification',
                doc: app.ui.help.docUrl('how-it-works/peak-assignment/#verifying-assignments')
              }"
            />
          </template>
          <template #body="{ data }">
            <BaseVerdictBadge
              v-if="verdictFor(data)"
              :record="verdictFor(data)"
              :conflict="conflictFor(data)"
              compact
            />
            <BaseVerdictBadge v-else :record="overlayFor(data)" inherited compact />
          </template>
        </Column>
      </DataTable>
    </div>

    <Dialog v-model:visible="configVisible" modal header="Assign peaks" :style="{ width: '26rem' }">
      <PeakAssignConfigForm :config="config" :pinned="['run_untargeted']" />
      <template #footer>
        <Button label="Cancel" text severity="secondary" @click="configVisible = false" />
        <Button label="Assign" icon="pi ph ph-magic-wand" :loading="submitting" @click="launch" />
      </template>
    </Dialog>
  </BaseTabbedPanel>
</template>

<style scoped>
/* A row the producing engine stated no tier on. Recessive, because it is the
   majority of the column on most runs - an engine typically tiers only the
   peaks it committed a formula to - and a column of full-strength dashes would
   read as missing data rather than as "no opinion here". */
.no-engine-tier {
  opacity: 0.4;
}

/* The panel body is a column: the launch-error banner and the tier strip take
   their natural height and the ledger takes the rest, so whatever is shown
   above the table shortens it instead of pushing it past the pane. */
:deep(.p-panel-content) {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
:deep(.p-message) {
  flex: 0 0 auto;
}
.ledger {
  flex: 1;
  min-height: 0;
  /* .col centres on space-between, which would drop the spinner and the load
     error to the bottom of the pane now that this column has room to spare. */
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

.empty {
  width: 100%;
  height: 220px;
}
.loading-region {
  width: 100%;
  min-height: 10rem;
}

.tier-strip {
  display: flex;
  flex-flow: row wrap;
  gap: 0.3rem;
  padding: 0 0.4rem;
  flex: 0 0 auto;
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
.tier-stat.reagent b {
  color: #8a5ed0;
}
.tier-stat.below b,
.tier-stat.unassigned b {
  color: var(--p-surface-500, #6f7889);
}

.formula {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.95rem;
}
.mech {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.82rem;
  opacity: 0.85;
  white-space: nowrap;
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

.pcorrect {
  font-variant-numeric: tabular-nums;
}
.pcorrect.uncal {
  opacity: 0.45;
}
.pcorrect .prov {
  color: var(--state-warning);
  margin-left: 0.05rem;
}
/* Adduct-corroboration marker beside P(correct). */
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

/* The cog sits at the far end of the tier-chip row, which wraps; pushing it
   with auto margin keeps it in the corner however many chips precede it. */
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
  /* Label first, control last, matching the verdict row below. Reversed here
     rather than reordered in the markup so the switch stays the first element
     in the panel, and so Tab reaches it before the verdict chips. */
  flex-direction: row-reverse;
}
.unfold-toggle label {
  cursor: pointer;
  opacity: 0.75;
}
.verdict-filter {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.view-menu-label {
  font-size: 0.8rem;
  opacity: 0.75;
}
.verdict-chips {
  display: flex;
  flex-flow: row wrap;
  gap: 0.3rem;
}
/* Deliberately the tier chip's shape: the two strips filter the same table, so
   a reader who has learned one has learned the other. */
.verdict-chip {
  font-size: 0.72rem;
  padding: 0.15rem 0.5rem;
  border-radius: 100px;
  border: 1px solid var(--p-content-border-color, #e3e6ec);
  background: transparent;
  color: inherit;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  transition:
    border-color 0.12s,
    background 0.12s;
}
.verdict-chip:hover {
  border-color: var(--p-primary-color, #6366f1);
}
.verdict-chip.active {
  border-color: var(--p-primary-color, #6366f1);
  background: color-mix(in srgb, var(--p-primary-color, #6366f1) 12%, transparent);
}

/* Unfolded isotopologue child row: indented substitution label under its M0. */
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
</style>
