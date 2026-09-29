<script setup>
import { ref, computed, watch, watchEffect, onMounted } from 'vue'
import { watchDebounced } from '@vueuse/core'

import FloatLabel from 'primevue/floatlabel'
import InputText from 'primevue/inputtext'
import InputNumber from 'primevue/inputnumber'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import ProgressSpinner from 'primevue/progressspinner'
import MultiSelect from 'primevue/multiselect'
import Button from 'primevue/button'

import { useApp } from '@/stores'
import { api } from '@/api'
import { BaseTierTag, BaseMatchTag } from '@/lib/base'
import { PopoverTargetCompoundAdd } from '@/lib/dialogs'
import { num } from '@/lib/formatters'
import { formatIsotopeFormula } from '@/lib/chem'
import { peakAssignmentEnabled } from '@/lib/features'
import { fetchProfilePreview, isFormulaRange, usePeakAssignParams } from '@/lib/peakAssignParams'

import { usePreview } from './preview.js'
import { canCurateHit, curationBodyForHit, hitKey, readLineOfHit } from './searchHit.js'

// On-demand composition search for the focused peak. Lives in the Sample view's
// pane under the spectrum, shown in place of the time series while "Re-search"
// is active (mounted only then, so it searches whenever it is on screen).
// Extracted from PanePeakAssign so the inspector stays a compact
// committed-assignment card.

const app = useApp()
const preview = usePreview()

const props = defineProps({
  height: {
    type: Number,
    required: true
  },
  // Mounted as a permanent pane (the legacy Sample layout) rather than as a
  // takeover of the time-series pane, so there is nothing to close and the
  // title says what the pane is instead of what dismisses it.
  embedded: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['close'])

// Root help card. The interaction wording differs between the legacy embedded
// placement (a peak browser sits to the left) and the Re-search takeover of
// the time-series pane; `embedded` never changes after mount.
const rootHelp = {
  message: `
    <h1>Composition Search</h1>
    <p>
    Search candidate compositions for the selected peak from its m/z value, the
    chosen ionization mechanisms and the allowed ranges of atom counts. The peak
    may be any line of a candidate's ion at least 1% of its brightest: a
    compound is found at its 13C, 34S or 81Br line as well as at its
    monoisotopic one.
    </p>
    ${
      props.embedded
        ? `<p>
          Select peaks by clicking rows in the peak browser to the left, or the
          vertical peak lines in the spectrum chart.
          </p>`
        : `<p>
          The search follows the focused peak: select peaks in the spectrum chart
          or the Assignments ledger. Close the search to return to the time series.
          </p>`
    }`,
  doc: app.ui.help.docUrl('how-it-works/peak-assignment/#searching-one-peak')
}

// One card for the whole results table: the icon-only column headers and the
// expandable isotope preview are the least guessable parts of the pane.
const resultsHelp = {
  message: `
    <h1>Search Results</h1>
    <p>
    Candidate compositions whose ions land within the m/z window.
    <b>DBE</b> is the degree of unsaturation.${
      peakAssignmentEnabled
        ? ` The seal column shows each
    candidate's confidence tier and sorts by its fit score, the atom column its
    chemical plausibility, and a flask names a match in a public reference
    database.`
        : ` The seal column shows each candidate's match score.`
    }
    A database icon marks formulas that already exist among your target compounds.
    </p>
    <p>
    A tag beside a formula, such as <code>[13C]</code> or <code>[81Br]</code>,
    marks a candidate found at another line of its ion than the monoisotopic
    one: the peak is that isotopologue of the compound, and the error is
    against that line.
    </p>
    <p>
    Expand a row to list the candidate's theoretical isotope lines under it,
    with each line's abundance and a crosshair on the one within tolerance of
    the searched peak, and click a line to preview it in the spectrum chart.
    The <b>+</b> button adds a candidate to the open target collection.
    </p>`,
  doc: peakAssignmentEnabled
    ? app.ui.help.docUrl('how-it-works/peak-assignment/#the-fit-score-a-pure-measurement')
    : app.ui.help.docUrl('how-it-works/matching/')
}

const sharePercent = new Intl.NumberFormat('en-US', {
  style: 'percent',
  maximumSignificantDigits: 2
})

/**
 * Hover text for the tag of a candidate read at another line of its ion. A
 * line at the monoisotopic nominal mass has no offset worth naming.
 *
 * @param {{name: string, offset: string, share: number|null}} line see readLineOfHit
 * @returns {string} the text
 */
function lineTooltip(line) {
  const place = [
    line.offset !== 'M0' ? line.offset : null,
    line.share != null ? `${sharePercent.format(line.share)} of its brightest` : null
  ].filter(Boolean)
  return (
    `Found at the ion's ${line.name} line${place.length ? ` (${place.join(', ')})` : ''}: ` +
    'the peak is this isotopologue of the compound, and the error is against that line'
  )
}

// The hand button's own card, rendered from the shared docs snippet rather than
// restated here: the same `_help/assignment-curation.md` is pulled into the
// user manual, so the in-app text and the manual cannot drift apart the way two
// hand-maintained copies of it did.
//
// Anchored on the column header, not on the button. Help cards register per
// element and are never unregistered (see stores/ui/help.js), so a directive
// inside a virtual-scrolled row body would leave one dead card behind for every
// row the table ever rendered. The same rule is why the header's directive
// hangs on a wrapper that outlives the glyph rather than on the glyph itself -
// see the column header.
const curationHelp = {
  title: 'Assigning by Hand',
  helpKey: 'assignment-curation',
  doc: app.ui.help.docUrl('how-it-works/peak-assignment/#assigning-a-peak-yourself')
}

// The search parameters are the shared ones. m/z precision and formula range
// mean the same thing here and in an assignment run's untargeted stage - the
// backend derives both from one pair of constants - so this pane binds the same
// persisted record the launcher dialogs bind, instead of keeping its own two
// values under its own storage key. Tuning them against one peak here is what
// the next run launches with, and a value set in a launcher is what this pane
// searches with. The store also carries the search debounce and the bounds,
// which used to be a second /params fetch from this component.
const store = usePeakAssignParams()
const params = store.params

const ionMechs = ref([])
const formulaRangeModel = ref(params.formula_ranges ?? '')
const results = ref([])
// Which peak the rows currently in `results` were found for. Kept beside the
// rows themselves and updated only where they are, because the two must never
// disagree: the write path below refuses to commit a hit against any other
// peak. Null whenever the table holds nothing anyone searched for.
const resultsPeakId = ref(null)
const totalMatches = ref(0)
const displayedMatches = ref(0)
const loading = ref(false)
const lastRequestParams = ref(null)

// The range validates against the shared rule rather than a copy of it: the
// launcher dialog binds the same field, so a string one surface would reject
// must not be able to arrive from the other.
const isFormulaRangeValid = computed(
  () => !formulaRangeModel.value || isFormulaRange(formulaRangeModel.value)
)

onMounted(() => store.ensureLoaded())

// Emptied, the field goes back to the chemistry profile's grid.
const updateFormulaRange = () => {
  if (!isFormulaRangeValid.value) return
  params.formula_ranges = formulaRangeModel.value?.trim() || null
}

// What an empty field searches with: the focused sample's chemistry profile,
// its element grid and its instrument's m/z window - what a run of the sample
// would use - asked of the server as the launcher asks it, and asked again
// when the sample or the chosen profile or context changes. A value typed in
// overrides it here as it would in the run.
const resolved = ref(null)
let resolveRequest = 0
watch(
  () => [app.data.sample.focusedId, params.profile, params.context],
  async ([sampleItemId, profile, context]) => {
    const request = ++resolveRequest
    resolved.value = null
    if (!sampleItemId) return
    try {
      const [record] = await fetchProfilePreview({ sampleItemId }, { profile, context })
      if (request === resolveRequest) resolved.value = record ?? null
    } catch {
      // The fields then say where a value would come from, and the search
      // waits for one typed in.
    }
  },
  { immediate: true }
)
const mzPrecision = computed(
  () => params.mz_precision_ppm ?? resolved.value?.mz_precision_ppm ?? null
)
const formulaRange = computed(() => params.formula_ranges ?? resolved.value?.element_ranges ?? null)
const FROM_PROFILE = 'From the chemistry profile'
const mzPrecisionPlaceholder = computed(() =>
  resolved.value?.mz_precision_ppm != null ? String(resolved.value.mz_precision_ppm) : FROM_PROFILE
)
const formulaRangePlaceholder = computed(() => resolved.value?.element_ranges ?? FROM_PROFILE)

// The reset control clears exactly the two fields this pane shows. The record
// is shared, so resetting everything from here would silently discard a peak
// ceiling or an alternatives count set in a launcher dialog - fields the user
// cannot see from this pane and would have no reason to expect it to touch.
const RESETTABLE = ['mz_precision_ppm', 'formula_ranges']

app.ui.notification.on('match_compositions_by_mz', (payload) => {
  if (payload.status === 'error') {
    loading.value = false
    return
  }
  if (!payload) return

  const isFocusedSample = payload?.data?.sample_item_id === app.data.sample.focusedId
  const isFocusedMz = payload?.data?.mz === app.data.peak.focused?.mz
  if (!isFocusedSample || !isFocusedMz) return

  if (payload.status === 'success') {
    if (payload.data?.data) {
      totalMatches.value = payload?.data?.total || 0
      displayedMatches.value = payload?.data?.results || 0

      // The two checks above already established that this payload is the
      // focused peak's, so this is the one place in the pane where a result set
      // is tied to a peak. Stamped with `peak_id` rather than the m/z the
      // payload carries: the ledger joins on peak_id, and it is the identity
      // the write path has to match.
      resultsPeakId.value = app.data.peak.focused?.peak_id ?? null

      results.value = payload.data.data.map((res) => {
        const existing = app.data.target.compound.list.filter(
          ({ target_compound_formula }) => target_compound_formula === res.target_compound_formula
        )
        return { ...res, existing, readAt: readLineOfHit(res), key: hitKey(res) }
      })
    }
    loading.value = false
  }
})

// Follow the store into the text box: the launcher dialog binds the same field
// and so does the reset button, so the committed range can change while this
// pane is mounted. A range put back to the profile's empties the box.
watch(
  () => params.formula_ranges,
  (newValue) => {
    const shown = newValue ?? ''
    if (formulaRangeModel.value !== shown) formulaRangeModel.value = shown
  }
)

watchEffect(() => {
  if (!store.loaded) return
  if (!app.data.sample.focused) return
  const ionMode = app.data.ionization.mode.list.find(
    (im) => im.ionization_mode_id === app.data.sample.focused.ionization_mode_id
  )
  ionMechs.value = ionMode.ionization_mechanism_ids.map((id) =>
    app.data.ionization.mechanism.list.find(
      ({ ionization_mechanism_id }) => id === ionization_mechanism_id
    )
  )
})

// Debounced composition search. The pane is only mounted while Re-search is
// active, so no explicit enable flag is needed: it searches for whatever peak
// is focused and re-runs when the peak or parameters change.
watchDebounced(
  () => {
    if (!store.loaded) return {}
    return {
      peakFocused: app.data.peak.focused ? app.data.peak.focused.mz : null,
      sampleId: app.data.sample.focusedId,
      mzPrecision: mzPrecision.value,
      formulaRange: formulaRange.value,
      ionMechanismIds: ionMechs.value.map((m) => m.ionization_mechanism_id).join(',')
    }
  },
  async (deps) => {
    if (!store.loaded || !deps.peakFocused || !deps.mzPrecision || !deps.formulaRange) {
      results.value = []
      resultsPeakId.value = null
      loading.value = false
      lastRequestParams.value = null
      return
    }
    const currentParams = JSON.stringify(deps)
    if (lastRequestParams.value === currentParams) {
      return
    }
    lastRequestParams.value = currentParams

    loading.value = true
    results.value = []
    resultsPeakId.value = null
    totalMatches.value = 0
    displayedMatches.value = 0

    await api.http.post(
      `/cheminfo/mz/match/sample/${deps.sampleId}`,
      {
        mz: app.data.peak.focused.mz,
        sample_item_id: deps.sampleId,
        ionization_mechanism_ids: ionMechs.value.map(
          ({ ionization_mechanism_id }) => ionization_mechanism_id
        ),
        mz_precision: deps.mzPrecision,
        formula_ranges: deps.formulaRange,
        // Any line of a candidate's ion may be the peak, not only its
        // monoisotopic one: a peak searched can be a compound's 13C or 81Br
        // line, and a candidate found at another line is tagged with it.
        isotopologues: true,
        match_params: app.data.match.params.typeDefaults
      },
      {
        use: 'read',
        type: 'match_compositions_by_mz'
      }
    )
  },
  {
    debounce: computed(() => store.debounceMs),
    deep: true,
    immediate: true
  }
)

function getIsotopeRows(data) {
  const maxIdx = data.children.reduce(
    (maxI, r, i, arr) =>
      (r.relative_abundance ?? 0) > (arr[maxI].relative_abundance ?? 0) ? i : maxI,
    0
  )
  const mainIsotopeAbundance = data.children[maxIdx]?.relative_abundance
  const mainIsotopeIntensity =
    app.data.peak.list.find((peak) => peak.mz === data.children[maxIdx]?.sample_peak_mz)?.height ||
    0
  return data.children.map((record) => ({
    ...record,
    close: (Math.abs(record.mz - app.data.peak.focused?.mz) * 1e6) / record.mz < mzPrecision.value,
    abundance_reference: mainIsotopeAbundance,
    intensity_reference: mainIsotopeIntensity
  }))
}

function knownCompoundLabel(known) {
  if (!known?.length) return ''
  const name = known[0]?.name?.length ? known[0].name : 'Unnamed'
  return known.length > 1 ? `${name} +${known.length - 1}` : name
}

function knownCompoundsTooltip(known) {
  if (!known?.length) return ''
  const names = known
    .map((k) => `${k?.name?.length ? k.name : 'Unnamed'}${k?.source ? ` (${k.source})` : ''}`)
    .join(', ')
  return `Known compound in public reference database: ${names}`
}

const expanded = ref({})

// --- Rows -------------------------------------------------------------------
// An expanded candidate lists its isotope lines as rows of the table, under
// it, rather than as a table nested in an expansion row. The results scroll
// virtually - the scroller renders the rows in view, sliced by a fixed row
// height - and a nested table is as tall as the candidate's isotope pattern,
// which that slicing cannot allow for: scrolling through a long pattern moved
// the slice past the candidate, the candidate left the page, the table shrank,
// and the view snapped back to the top. A line as a row of its own is one
// fixed-height row among the others, the arrangement the peak ledger unfolds
// its isotopologues in for the same reason.
//
// The sort is therefore the pane's, not the table's: PrimeVue sorting the flat
// rows would tear the lines away from their candidate. `lazy` hands sorting
// back, and `rows` orders the candidates and puts each one's lines under it.
const ROW_HEIGHT = 35.5
const scrollHeightPx = computed(() => Math.max(120, props.height - 120))
const sortField = ref(peakAssignmentEnabled ? 'fit_score' : 'match_score')
const sortOrder = ref(-1)

// Numeric collation, as PrimeVue's own sort had it: C9H14O4 before C10H16O4.
const collator = new Intl.Collator(undefined, { numeric: true })
const isBlank = (value) => value == null || value === ''

// What a column sorts on: the field it names, read along its path, except the
// database column, which counts the target compounds it names.
function sortValue(row, field) {
  if (field === 'existing') return row.existing?.length ?? 0
  return field.split('.').reduce((value, key) => value?.[key], row)
}

// One column's order, blanks last either way. The sort is stable, so
// candidates the column cannot tell apart keep the order the search gave them.
function compareBy(field, order) {
  const dir = order === -1 ? -1 : 1
  return (a, b) => {
    const av = sortValue(a, field)
    const bv = sortValue(b, field)
    if (isBlank(av) && isBlank(bv)) return 0
    if (isBlank(av)) return 1
    if (isBlank(bv)) return -1
    if (typeof av === 'string' && typeof bv === 'string') return collator.compare(av, bv) * dir
    return av < bv ? -dir : av > bv ? dir : 0
  }
}

// A candidate's isotope lines, lightest first, as rows under it: each keeps
// its candidate (`parent`) and has a key of its own.
function linesOf(hit) {
  return getIsotopeRows(hit)
    .sort((a, b) => a.mz - b.mz)
    .map((line) => ({ ...line, key: `${hit.key}|${line.mz}`, parent: hit }))
}

const rows = computed(() => {
  const candidates = [...results.value]
  if (sortField.value) candidates.sort(compareBy(sortField.value, sortOrder.value))
  return candidates.flatMap((hit) => (expanded.value[hit.key] ? [hit, ...linesOf(hit)] : [hit]))
})

// A line's name, as the peak inspector names an isotopologue row: M0, [13C],
// [81Br]2, or [14N] for a labelled reagent's unlabelled remainder.
const lineName = (line) =>
  formatIsotopeFormula(line.target_isotope_formula, line.parent.target_ion_formula) || '-'

// The line previewed in the spectrum chart is the table's selection, so a row
// takes focus and the keyboard reaches the lines: the arrow keys move between
// rows, and Enter or Space previews a line as a click does. Selecting the line
// already previewed takes the preview away. A candidate is not a line, and
// selecting one changes nothing.
const previewedLine = computed({
  get: () => preview.peak ?? null,
  set: (row) => {
    if (!row) preview.peak = null
    else if (row.parent) preview.peak = row
  }
})
const rowClass = (row) => (row.parent ? 'line-row' : undefined)

const fitPercent = new Intl.NumberFormat('en-US', {
  style: 'percent',
  minimumFractionDigits: 0,
  maximumFractionDigits: 0
})
const formatFit = (value) =>
  value != null && !Number.isNaN(value) ? fitPercent.format(value) : '-'

// --- Assign a search hit to the focused peak ------------------------------
// The write path out of the search: a composition the user found here is
// committed onto the peak's own ledger row, marked as a manual assignment,
// instead of only being added to a target collection (which feeds the legacy
// rematch pipeline and reaches the ledger only via a whole new run).

// The row the assignment lands on. Every detected peak of a run has one - an
// unexplained peak carries an `unassigned` placeholder - so this is null only
// when no run covers the focused peak at all.
const assignTarget = computed(() =>
  app.data.peakAssignment.peak.forPeak(app.data.peak.focused?.peak_id)
)

const assigning = ref(null) // key of the hit being committed
const assignDenied = ref(false) // 403: not an editor on this sample
// A ledger derived from the batch peaks (run engine 'batch') has no rows to
// commit a composition onto; the server answers 409, so the hand is withheld.
const derivedRun = computed(() => app.data.peakAssignment.peak.run?.engine === 'batch')

// The results outlive the peak they were found for, so the write has to be
// pinned to that peak rather than to whatever is focused now. Focus moves the
// instant a peak is clicked and `assignTarget` follows it synchronously, but
// the table is only replaced when the debounced search callback finally runs -
// DEBOUNCE_DELAY_MS later, 800 ms by default. For that whole window the rows on
// screen belong to the previous peak while the hand button already aims at the
// new peak's ledger row, and `set_assignment` commits the composition it is
// given without ever comparing it to the peak's m/z. Unguarded, one click there
// records a formula hundreds of daltons off on the newly focused peak, tiered
// from the other peak's fit score, and demotes the isotopologues of the formula
// that peak really had - silently, with a success toast.
//
// Compared against the target row's own peak, not against the focused peak:
// the question is whether the row about to be written is the row the results
// were found for, and answering it off the row itself does not depend on two
// computeds agreeing about focus.
const resultsMatchTarget = computed(
  () =>
    resultsPeakId.value != null &&
    assignTarget.value != null &&
    String(assignTarget.value.sample_peak_id) === String(resultsPeakId.value)
)

const assignTooltip = computed(() => {
  if (!assignTarget.value) return 'No assignment run covers this peak yet - assign the sample first'
  if (derivedRun.value) {
    return 'This ledger is derived from the batch peaks - assign the sample to edit it'
  }
  if (!resultsMatchTarget.value)
    return 'These results are for the previously selected peak - the search for this one is still coming'
  return 'Assign this composition to the selected peak, as a manual assignment'
})

async function assignToPeak(hit) {
  // Re-checked here and not only on the button: `disabled` lands on the next
  // render, so a click can already be on its way when the focus changes.
  if (!assignTarget.value || !resultsMatchTarget.value || !canCurateHit(hit)) return
  if (assigning.value !== null) return
  assigning.value = hitKey(hit)
  try {
    await app.data.peakAssignment.peak.curate(
      assignTarget.value.peak_assignment_id,
      curationBodyForHit(hit)
    )
  } catch (error) {
    // The http layer already toasts; only 403 changes the UI (hide the control).
    if (error?.response?.status === 403) assignDenied.value = true
  } finally {
    assigning.value = null
  }
}

watch(
  () => app.data.sample.focusedId,
  () => {
    assignDenied.value = false
  }
)
</script>

<template>
  <!-- Embedded is the legacy Sample layout, where this pane sits permanently
       beside the ledger: a sample with no detected peaks has nothing to search,
       so the pane is absent rather than showing a "No peak selected" card.
       As a takeover of the time-series pane it always renders, otherwise
       "Re-search" would open onto nothing with no way back. -->
  <div
    class="search-pane"
    v-if="!embedded || app.data.peak.list.length > 0"
    v-help.top="rootHelp"
    :style="{ '--row-height': `${ROW_HEIGHT}px` }"
  >
    <header class="search-head">
      <div class="search-title">
        <span class="pi ph ph-magnifying-glass" />
        <span>{{ embedded ? 'Peak Assign' : 'Re-search' }}</span>
        <span v-if="app.data.peak.focused" class="search-sub">
          peak {{ num.mz.format(app.data.peak.focused.mz) }} &middot; showing
          {{ displayedMatches }} / {{ totalMatches }}
          {{ totalMatches === 1 ? 'compound' : 'compounds' }}
        </span>
      </div>
      <Button
        v-if="!embedded"
        icon="pi pi-times"
        size="small"
        text
        severity="secondary"
        v-tooltip.left="'Close search (show time series)'"
        @click="emit('close')"
      />
    </header>
    <menu class="topbar">
      <FloatLabel
        style="flex: 0 0 80px"
        :pt="
          app.ui.help.bottom(`
            <h1>m/z Precision</h1>
            <p>
            The mass tolerance of the search, in ppm: a candidate is kept when a
            theoretical isotope of its ion lands within this window of the peak's
            m/z. Widening it finds more candidates, but more ambiguous ones.
            Left empty, it is the window a run of this sample would use, set by
            its chemistry profile and instrument.
            </p>
          `)
        "
      >
        <InputNumber
          v-model="params.mz_precision_ppm"
          inputId="mzPrecision"
          :min="1"
          :max="store.limits.max_mz_precision_ppm"
          :placeholder="mzPrecisionPlaceholder"
          fluid
        />
        <label for="mzPrecision">m/z precision</label>
      </FloatLabel>
      <FloatLabel
        style="flex-grow: 1"
        :pt="
          app.ui.help.bottom(`
            <h1>Formula Range</h1>
            <p>
            Allowed element counts for candidate formulas, as space-separated
            ranges &mdash; e.g. <code>C0-80 H0-160 [15N]0-1</code>, isotopes in
            brackets. Narrowing the ranges makes the search faster and keeps
            chemically irrelevant candidates out. Left empty, it is the grid of
            the sample's chemistry profile.
            </p>
          `)
        "
      >
        <InputText
          v-model="formulaRangeModel"
          id="formulaRange"
          fluid
          :invalid="!isFormulaRangeValid"
          :placeholder="formulaRangePlaceholder"
          @blur="updateFormulaRange"
          @keydown.enter="updateFormulaRange"
          v-tooltip.bottom="{
            value: 'Format: Element + range, e.g. C0-80 H0-160 [15N]0-1 ^N0-1',
            showDelay: 500
          }"
        />
        <label for="formulaRange">formula range</label>
      </FloatLabel>
      <FloatLabel
        style="min-width: 100px; max-width: 200px"
        :pt="
          app.ui.help.bottom_end(
            `
            <h1>Ionization Mechanisms</h1>
            <p>
            Which charge-forming reactions (adducts) to consider when turning a
            neutral formula into a detectable ion. Preselected from the sample's
            ionization mode; narrow or widen the set to steer the search.
            </p>
          `,
            { doc: app.ui.help.docUrl('concepts/#ionization-modes-and-mechanisms') }
          )
        "
      >
        <MultiSelect
          id="ionmechs"
          v-model="ionMechs"
          dataKey="ionization_mechanism_id"
          :options="app.data.ionization.mechanism.list"
          optionLabel="ionization_mechanism"
          fluid
        />
        <label for="ionmechs">Ion. Mechanisms</label>
      </FloatLabel>
      <!-- These two persist and are shared with the assignment launchers, so
           the way back to the shipped defaults has to be reachable from here
           too. Disabled while both are already at their default, which makes it
           the answer to "have I changed these?" as well as the way to undo. -->
      <Button
        icon="pi ph ph-arrow-counter-clockwise"
        size="small"
        text
        severity="secondary"
        class="reset-params"
        aria-label="Reset search parameters to defaults"
        :disabled="store.isDefault(RESETTABLE)"
        v-tooltip.bottom="'Reset m/z precision and formula range to defaults'"
        @click="store.reset(RESETTABLE)"
      />
    </menu>
    <!-- The rows are `rows`: the candidates in the pane's sort, each expanded
         one followed by its isotope lines (see the script). The expander is
         PrimeVue's, and with no #expansion template it only flips
         `expanded`; a line's own expander is hidden. -->
    <DataTable
      v-if="!loading && results.length > 0"
      :value="rows"
      dataKey="key"
      lazy
      v-model:sortField="sortField"
      v-model:sortOrder="sortOrder"
      scrollable
      :scrollHeight="`${scrollHeightPx}px`"
      size="small"
      v-model:expandedRows="expanded"
      :virtualScrollerOptions="{ itemSize: ROW_HEIGHT }"
      :rowClass="rowClass"
      selectionMode="single"
      :metaKeySelection="false"
      v-model:selection="previewedLine"
      :pt="app.ui.help.top(resultsHelp)"
    >
      <Column expander />
      <Column field="target_compound_formula" header="Formula" sortable>
        <template #body="{ data }">
          <span v-if="data.parent" class="line-cell">
            <span
              class="pi ph ph-crosshair line-close"
              :class="{ hidden: !data.close }"
              v-tooltip.left="data.close ? 'Within tolerance of the searched peak' : null"
            />
            {{ lineName(data) }}
            <span class="line-share">{{
              num.relativeAbundance.format(data.relative_abundance)
            }}</span>
          </span>
          <template v-else>
            {{ data.target_compound_formula }}
            <span
              v-if="data.readAt"
              class="line-tag"
              v-tooltip.top="{ value: lineTooltip(data.readAt), showDelay: 300 }"
              >{{ data.readAt.name }}</span
            >
          </template>
        </template>
      </Column>
      <Column field="cheminfo.target_compound_unsaturation" sortable>
        <template #header>
          <span v-tooltip="{ value: 'Degree of unsaturation', showDelay: 500 }"><b>DBE</b></span>
        </template>
      </Column>
      <Column field="cheminfo.target_isotope_mz" header="Isotope m/z" sortable>
        <template #body="{ data }">
          {{ num.mz.format(data.parent ? data.mz : data.cheminfo.target_isotope_mz) }}
        </template>
      </Column>
      <Column field="cheminfo.ionization_mechanism.ionization_mechanism" header="Mech." sortable />
      <Column field="cheminfo.target_isotope_mz_error_ppm" header="Error (ppm)" sortable>
        <template #body="{ data }">
          {{
            num.mzError.format(
              data.parent ? data.match_mz_error : data.cheminfo.target_isotope_mz_error_ppm
            )
          }}
        </template>
      </Column>
      <Column v-if="peakAssignmentEnabled" field="fit_score" sortable>
        <template #header>
          <span
            class="pi ph ph-seal-check"
            v-tooltip="{ value: 'Confidence tier, sorted by fit score', showDelay: 500 }"
          />
        </template>
        <template #body="{ data }">
          <!-- A line has no tier of its own: it shows how well it matched. -->
          <BaseMatchTag
            v-if="data.parent"
            :match-score="data.match_score"
            :match-category="data.match_category"
            :alarming="data.alarming"
            nofade
          />
          <BaseTierTag v-else :tier="data.tier" :evidence="data.evidence" :source="data.source" />
        </template>
      </Column>
      <Column v-if="peakAssignmentEnabled" field="plausibility" sortable>
        <template #header>
          <span
            class="pi ph ph-atom"
            v-tooltip="{ value: 'Chemical plausibility (Seven Golden Rules)', showDelay: 500 }"
          />
        </template>
        <template #body="{ data }">
          <template v-if="!data.parent">
            {{ data.plausibility != null ? formatFit(data.plausibility) : '—' }}
          </template>
        </template>
      </Column>
      <!-- Legacy scoring: what this search has always reported. The backend
           only computes fit/tier/plausibility when the feature is enabled. -->
      <Column v-else field="match_score" sortable>
        <template #header>
          <span
            class="pi ph ph-seal-percent"
            v-tooltip="{ value: 'Match score', showDelay: 500 }"
          />
        </template>
        <template #body="{ data }">
          <BaseMatchTag
            :match-score="data?.match_score"
            :match-category="data?.match_category"
            :alarming="data?.alarming"
            nofade
          />
        </template>
      </Column>
      <Column field="existing" sortable>
        <template #header>
          <span
            class="pi pi-info-circle"
            v-tooltip.left="{ value: 'Compound info', showDelay: 500 }"
          />
        </template>
        <template #body="{ data }">
          <span
            v-if="data.existing?.length > 0"
            class="ph pi ph-database"
            v-tooltip.left="
              `Found in DB: ${data.existing
                .map(
                  (comp) =>
                    `${comp?.target_compound_name?.length > 0 ? comp.target_compound_name : 'Unnamed'}`
                )
                .join(', ')}`
            "
          />
        </template>
      </Column>
      <!-- Known-compound annotation arrived with peak-centric assignment; with
           the feature off the search results are the legacy ones, so the column
           (and its header) stays out of the table entirely rather than sitting
           there empty. -->
      <Column v-if="peakAssignmentEnabled">
        <template #header>
          <span
            class="pi ph ph-flask"
            v-tooltip.left="{
              value: 'Known compound (public reference database)',
              showDelay: 500
            }"
          />
        </template>
        <template #body="{ data }">
          <span
            v-if="data.cheminfo?.known_compounds?.length"
            class="known-identity"
            v-tooltip.left="{
              value: knownCompoundsTooltip(data.cheminfo.known_compounds),
              showDelay: 300
            }"
          >
            <span class="pi ph ph-flask" />
            {{ knownCompoundLabel(data.cheminfo.known_compounds) }}
          </span>
        </template>
      </Column>
      <Column>
        <!-- Also the anchor for the curation help card, which is why the icon
             follows the control it explains and goes with it for a viewer who
             may not curate at all.
             The card hangs on the wrapper and the meaning on the glyph inside
             it, because the two have different lifetimes. `assignDenied` flips
             when a write comes back 403, and a help card whose element goes
             away is never unregistered (see stores/ui/help.js) - it would sit
             in the store's list for the rest of the session holding a mouse
             watcher on an element nobody can reach. The wrapper is gated on the
             build-time feature flag alone, which cannot change after mount;
             with the glyph gone it collapses to nothing, so a viewer who may
             not curate still gets no control and no card. -->
        <template #header>
          <span v-if="peakAssignmentEnabled" class="curate-header" v-help.left="curationHelp">
            <span
              v-if="!assignDenied"
              class="pi ph ph-hand-pointing"
              v-tooltip.left="{ value: 'Assign to the selected peak', showDelay: 500 }"
            />
          </span>
        </template>
        <template #body="{ data }">
          <div v-if="!data.parent" class="row-actions">
            <!-- The Button is disabled when no run covers the peak, or while
                 the rows on screen still belong to the previously focused one,
                 and a disabled PrimeVue button receives no mouse events - so
                 the tooltip explaining why has to hang on a wrapper. A hit with
                 no adduct is a different case: there is no state in which it
                 could be committed, so it gets no control at all rather than a
                 permanently dead one. -->
            <span
              v-if="peakAssignmentEnabled && !assignDenied && canCurateHit(data)"
              v-tooltip.left="{ value: assignTooltip, showDelay: 300 }"
            >
              <Button
                icon="pi ph ph-hand-pointing"
                size="small"
                text
                severity="secondary"
                :disabled="!assignTarget || !resultsMatchTarget || assigning !== null || derivedRun"
                :loading="assigning === hitKey(data)"
                :aria-label="`Assign ${data.target_compound_formula} to the selected peak`"
                @click="assignToPeak(data)"
              />
            </span>
            <PopoverTargetCompoundAdd
              :formula="data.target_compound_formula"
              :formula-editable="false"
            />
          </div>
        </template>
      </Column>
    </DataTable>
    <div v-else-if="!app.data.peak.focused" class="center search-placeholder">
      <div class="col" style="gap: 1rem; max-width: 45ch; text-align: center">
        <strong> <span class="pi ph ph-info" /> No peak selected</strong>
        <i style="opacity: 0.6">
          Select a peak in the spectrum or ledger to search compositions for it.
        </i>
      </div>
    </div>
    <div v-else-if="!loading && results.length === 0" class="center search-placeholder">
      <div class="col" style="gap: 1rem; max-width: 45ch; text-align: center">
        <strong> <span class="pi ph ph-info" /> No results found </strong>
        <i style="opacity: 0.6"> Consider broadening the m/z precision or formula range. </i>
      </div>
    </div>
    <div v-if="loading" class="center search-placeholder">
      <ProgressSpinner />
    </div>
  </div>
</template>

<style scoped>
.search-pane {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  height: 100%;
  width: 100%;
  padding: 0.5rem 0.75rem;
  overflow: hidden;
}
.search-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}
.search-title {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  font-weight: 600;
}
.search-sub {
  font-weight: 400;
  opacity: 0.6;
  font-size: 0.85rem;
}
.topbar {
  justify-content: space-between;
  padding: 0;
  margin: 0;
  display: flex;
  flex-flow: row nowrap;
  gap: 1rem;
  width: 100%;
}

/* Sits with the fields it resets rather than stretched to their height: the bar
   stretches its children by default, and FloatLabel wraps each input in a box
   as tall as the row. */
.reset-params {
  flex: 0 0 auto;
  align-self: center;
}
/* The line a candidate was read at, beside its formula: quiet enough that a
   table of them still reads as formulas, present enough that a row found at a
   13C line is not taken for the compound's own mass. */
.line-tag {
  margin-left: 0.35rem;
  padding: 0 0.3rem;
  border-radius: 4px;
  font-size: 0.75rem;
  background: var(--p-content-hover-background, rgba(127, 127, 127, 0.12));
  white-space: nowrap;
}
/* An expanded candidate's isotope line, a row under it: indented, and a click
   previews it in the spectrum chart. Its row has the expander column like
   every row, with nothing to expand, and the virtual scroller's row height,
   which its own content would fall short of. A candidate's row is selectable
   only so the keyboard can pass through it, and says so by its cursor. */
.line-cell {
  display: inline-flex;
  align-items: baseline;
  gap: 0.35rem;
  padding-left: 0.9rem;
  white-space: nowrap;
}
.line-close.hidden {
  visibility: hidden;
}
.line-share {
  opacity: 0.6;
  font-size: 0.85em;
}
.search-pane :deep(tr.line-row) {
  height: var(--row-height);
}
.search-pane :deep(.p-datatable-tbody > tr:not(.line-row)) {
  cursor: default;
}
.search-pane :deep(tr.line-row .p-datatable-row-toggle-button) {
  visibility: hidden;
}
/* The element the curation help card is registered on. It is a hook for the
   directive and nothing else, so with its glyph gone it takes up no space -
   which is what keeps a card that must not unmount from being reachable by a
   viewer the control has been taken away from. */
.curate-header {
  display: inline-flex;
}
.row-actions {
  display: flex;
  align-items: center;
  gap: 0.15rem;
  justify-content: flex-end;
}
.known-identity {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  color: var(--p-primary-color);
  white-space: nowrap;
  max-width: 22ch;
  overflow: hidden;
  text-overflow: ellipsis;
}
.search-placeholder {
  flex-grow: 1;
  display: grid;
  place-items: center;
}
</style>
