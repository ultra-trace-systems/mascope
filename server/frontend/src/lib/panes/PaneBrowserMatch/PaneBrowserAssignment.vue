<script setup>
import { ref, reactive, computed, watch } from 'vue'

import Button from 'primevue/button'
import Select from 'primevue/select'
import Dialog from 'primevue/dialog'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import ToggleSwitch from 'primevue/toggleswitch'

import { getApiErrorMessage, isRefusedRequest } from '@/api/utils'
import {
  BaseCopyableField,
  BaseLoadError,
  BaseRunProvenance,
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

const app = useApp()

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
const verdictFor = (row) =>
  row?.assigned_formula ? app.data.peakAssignment.verification.forAssignment(row) : null

// Verdict filter (single-select). "unverified" = no current verdict.
const VERDICT_FILTERS = [
  { value: 'all', label: 'All verdicts' },
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'unsure', label: 'Unsure' },
  { value: 'unverified', label: 'Unverified' }
]
const verdictFilter = ref('all')

// --- Run selector -----------------------------------------------------------

// One dropdown option per run, labelled with its ordinal and status. The
// ellipsis marks only runs still in flight - failed/cancelled runs are done,
// just not completed (mirrors the backend's non-terminal statuses).
const IN_FLIGHT_STATUSES = ['pending', 'running', 'importing']

// Label a run from the run itself rather than from a property carried on the
// option copy. The `#value` slot is handed the raw v-model value - the record
// straight off the run store - not the matched option, so a label that lived
// only on the option would render blank in the closed selector.
function runLabel(run) {
  if (!run) return ''
  const list = runs.value.list
  const index = list.findIndex((r) => r.peak_assignment_run_id === run.peak_assignment_run_id)
  const ordinal = index === -1 ? '' : `#${list.length - index} · `
  return `${ordinal}${run.status}${IN_FLIGHT_STATUSES.includes(run.status) ? '…' : ''}`
}

const runOptions = computed(() => runs.value.list.map((run) => ({ ...run, _label: runLabel(run) })))

const selectedRun = computed({
  get: () => runs.value.focused,
  set: (run) => (run ? runs.value.focus(run) : runs.value.unfocus())
})

// --- Launch a run -----------------------------------------------------------

const configVisible = ref(false)
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

// Fold isotopologue satellites by default: one row per assigned formula (M0)
// plus unassigned/reagent peaks, which keeps this a flat, fixed-height list
// compatible with virtual scrolling. Toggle to unfold (see below).
const showIsotopologues = ref(false)

// --- Sorting ----------------------------------------------------------------

// The sort is the pane's, not the table's. PrimeVue sorts the flat row array it
// is handed, which tears every isotopologue satellite away from the parent it
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
// unfolded, each parent's iso_child satellites are inserted right after it,
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
        (verdictFor(row)?.verdict ?? 'unverified') === verdictFilter.value
    )
    .map((row) => ({
      ...row,
      tierRank: tierRank(row.tier),
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
        // Adduct corroboration is written onto the M0 winner alone: a satellite
        // is the same ion measured at another isotope, not a second sighting of
        // the compound, so it never carries a count of its own. The evidence is
        // about the formula the family shares, so the satellite shows its
        // parent's count and the marker says where it came from.
        const own = child.corroboration_adducts ?? child.provenance?.corroboration?.n_adducts
        return {
          ...child,
          tierRank: parent.tierRank,
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

// A borrowed count is parenthesised, so a satellite does not read at a glance as
// a peak seen through several adducts in its own right.
const corrobLabel = (row) =>
  row.corrobInherited ? `(${row.corrobAdducts})` : `${row.corrobAdducts}`

// Tooltip for the adduct-corroboration marker. A satellite shows the count its
// M0 was corroborated by, so it has to say both that the evidence is the
// family's and that the boost is in the M0's P(correct) - the engine folds it
// into the record carrying the corroboration and never into a satellite's, so
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

// Isotopologue satellites folded under a formula's M0.
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
      {
        icon: 'pi ph ph-caret-left',
        tooltip: 'Back to the batch peaks',
        action: () => app.data.sample.unfocus()
      },
      ...(batch
        ? [
            {
              icon: 'pi pi-hashtag',
              label: prettyTrim(batch.sample_batch_name, 25),
              disabled: true,
              tooltip: `Batch: ${batch.sample_batch_name}`
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
        tier chips to filter by confidence, and <b>Assign peaks</b> to launch a
        new run.
        </p>`,
        { doc: app.ui.help.docUrl('how-it-works/peak-assignment/') }
      )
    "
  >
    <template #menu>
      <div class="menu-row">
        <Select
          v-if="runOptions.length"
          v-model="selectedRun"
          :options="runOptions"
          optionLabel="_label"
          dataKey="peak_assignment_run_id"
          size="small"
          placeholder="Select run"
          style="min-width: 15rem"
          :pt="
            app.ui.help.bottom(
              { title: 'Assignment Runs', helpKey: 'assignment-runs' },
              { doc: app.ui.help.docUrl('how-it-works/peak-assignment/#assignment-runs') }
            )
          "
        >
          <!-- Which engine produced the run travels with the run itself, in
               both the closed selector and the open list: a published run is
               first-class and the ledger defaults to the newest completed run
               whatever produced it, so the engine has to be visible wherever a
               run is named rather than only after opening a menu. -->
          <template #value="{ value, placeholder }">
            <span v-if="value" class="run-option">
              <span class="run-name">{{ runLabel(value) }}</span>
              <BaseRunProvenance :run="value" compact />
            </span>
            <span v-else>{{ placeholder }}</span>
          </template>
          <template #option="{ option }">
            <span class="run-option">
              <span class="run-name">{{ option._label }}</span>
              <BaseRunProvenance :run="option" />
            </span>
          </template>
        </Select>
        <div
          v-if="runs.list.length"
          class="unfold-toggle"
          v-tooltip.top="'Show isotopologue peaks as indented rows under their compound'"
          v-help.bottom="{
            message: `
              <h1>Isotopologue Rows</h1>
              <p>
              By default the ledger keeps one row per assigned formula, its
              isotopologue satellite peaks folded into the <b>+N</b> marker.
              Toggle to unfold them as indented rows under their main peak (M0).
              </p>`
          }"
        >
          <ToggleSwitch v-model="showIsotopologues" inputId="unfold-iso" />
          <label for="unfold-iso">Isotopologues</label>
        </div>
        <Select
          v-if="runs.list.length"
          v-model="verdictFilter"
          :options="VERDICT_FILTERS"
          optionLabel="label"
          optionValue="value"
          size="small"
          style="min-width: 9rem"
          v-tooltip.top="'Filter by verification verdict'"
          :pt="
            app.ui.help.bottom(
              { title: 'Verification', helpKey: 'assignment-verification' },
              { doc: app.ui.help.docUrl('how-it-works/peak-assignment/#verifying-assignments') }
            )
          "
        />
        <!-- Only when the empty state below is not carrying the call to action
             itself: two identical buttons a few centimetres apart read as two
             different things. The error state has no button of its own, so the
             toolbar keeps one there - a failed run list must not also cost the
             user the way to start a run. -->
        <Button
          v-if="runs.list.length || runs.error"
          label="Assign peaks"
          icon="pi ph ph-magic-wand"
          size="small"
          :disabled="!app.data.sample.focused"
          @click="configVisible = true"
          :pt="
            app.ui.help.bottom(
              `
              <h1>Assign Peaks</h1>
              <p>
              Launches an assignment run for this sample: every peak is matched
              against the known target library first, then optionally through the
              untargeted composition search. Opens the run configuration.
              </p>`,
              { doc: app.ui.help.docUrl('how-it-works/peak-assignment/#the-two-stages') }
            )
          "
        />
      </div>
    </template>

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
      </div>

      <div v-if="assignments.pending" class="center loading-region">
        <ProgressSpinner />
      </div>
      <!-- Inline rather than through the panel's :error, so the run selector and
           tier strip above stay usable while only the ledger is unavailable. -->
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
                 satellite count rides in the slot, outside what gets copied. -->
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
              :fit-score="data.fit_score"
              :role="data.role"
              :source="data.source"
            />
          </template>
        </Column>
        <Column field="pCorrect" sortable style="min-width: 6.5rem">
          <template #header>
            <span
              v-tooltip.top="
                'Calibrated probability the assignment is correct. Database-stage, calibrated instruments only; untargeted / uncalibrated show —.'
              "
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
            <span
              v-else
              class="pcorrect uncal"
              v-tooltip.top="
                data.source === 'untargeted'
                  ? 'Untargeted assignment - no calibrated probability'
                  : 'No calibration curve for this instrument'
              "
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
            <BaseVerdictBadge :record="verdictFor(data)" compact />
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
  color: var(--p-green-600, #1f9d63);
}
.tier-stat.candidate b {
  color: var(--p-amber-600, #c9861f);
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
  color: var(--p-orange-500, #f59e0b);
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
  color: var(--p-teal-600, #0d9488);
}
.corrob-mark .pi {
  font-size: 0.75rem;
}

.menu-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

/* Run label + its provenance chips, in the closed selector and the open list. */
.run-option {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  min-width: 0;
}
.run-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.unfold-toggle {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.8rem;
  white-space: nowrap;
}
.unfold-toggle label {
  cursor: pointer;
  opacity: 0.75;
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

.toggle-row {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
}
.toggle-row label {
  display: flex;
  flex-direction: column;
  font-size: 0.9rem;
}
.toggle-row small {
  opacity: 0.6;
  font-size: 0.75rem;
}
</style>
