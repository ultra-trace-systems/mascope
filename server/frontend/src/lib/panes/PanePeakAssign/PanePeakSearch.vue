<script setup>
import { ref, reactive, computed, watch, watchEffect, onMounted } from 'vue'
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
import { peakAssignmentEnabled } from '@/lib/features'

import { usePreview } from './preview.js'
import { canCurateHit, curationBodyForHit, hitKey } from './searchHit.js'

// On-demand composition search for the focused peak. Lives in the Sample view's
// bottom pane, shown in place of the time series while "Re-search" is active
// (mounted only then, so it searches whenever it is on screen). Extracted from
// PanePeakAssign so the inspector stays a compact committed-assignment card.

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
    chosen ionization mechanisms and the allowed ranges of atom counts.
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
  doc: app.ui.help.docUrl('how-it-works/peak-assignment/#the-two-stages')
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
    candidate's fit score and confidence tier, the atom column its chemical
    plausibility, and a flask names a match in a public reference database.`
        : ` The seal column shows each candidate's match score.`
    }
    A database icon marks formulas that already exist among your target compounds.
    </p>
    <p>
    Expand a row to see the candidate's full theoretical isotope pattern, and
    click an isotope row to preview it in the spectrum chart. The <b>+</b>
    button adds a candidate to the open target collection.
    </p>`,
  doc: peakAssignmentEnabled
    ? app.ui.help.docUrl('how-it-works/peak-assignment/#the-fit-score-a-pure-measurement')
    : app.ui.help.docUrl('how-it-works/matching/')
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

const PARAMS_STORAGE_KEY = 'mascope.peakAssign.params'

function loadStoredParams() {
  try {
    const stored = localStorage.getItem(PARAMS_STORAGE_KEY)
    if (stored) return JSON.parse(stored)
  } catch {}
  return null
}

function saveParams(mzPrecision, formulaRange) {
  try {
    localStorage.setItem(PARAMS_STORAGE_KEY, JSON.stringify({ mzPrecision, formulaRange }))
  } catch {}
}

// Fallback debounce for the search, used until /params answers. watchDebounced
// evaluates the delay before the callback's own guards run -- including on the
// immediate pass during setup, when chemConfig is still null.
const DEFAULT_DEBOUNCE_DELAY_MS = 800

const chemConfig = ref(null)
const ionMechs = ref([])
const params = reactive({
  mzPrecision: null,
  formulaRange: null
})
const formulaRangeModel = ref('')
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

// Regex pattern for formula range validation: "C0-100 H0-100 Cl0-10"
const ELEMENT_PATTERN = '(?:[A-Z][a-z]?|\\^[A-Z][a-z]?|\\[\\d*[A-Z][a-z]?\\])'
const RANGE_PATTERN = '\\d+-\\d+'
const FORMULA_RANGE_PATTERN = new RegExp(
  `^(${ELEMENT_PATTERN}${RANGE_PATTERN})(\\s+${ELEMENT_PATTERN}${RANGE_PATTERN})*$`
)

const isFormulaRangeValid = computed(() => {
  if (!formulaRangeModel.value) return true
  return FORMULA_RANGE_PATTERN.test(formulaRangeModel.value.trim())
})

onMounted(() => {
  api.http
    .get('/params', { type: 'read_params' })
    .then(({ data }) => {
      chemConfig.value = data?.data?.params?.cheminfo_config
      if (chemConfig.value) {
        const stored = loadStoredParams()
        params.mzPrecision = stored?.mzPrecision ?? chemConfig.value.DEFAULT_MZ_PRECISION
        params.formulaRange = stored?.formulaRange ?? chemConfig.value.DEFAULT_FORMULA_RANGE
        formulaRangeModel.value = params.formulaRange
      }
    })
    .catch((err) => {
      console.error('Error fetching params:', err)
    })
})

const updateFormulaRange = () => {
  if (isFormulaRangeValid.value) {
    params.formulaRange = formulaRangeModel.value.trim()
  }
}

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
        return { ...res, existing }
      })
    }
    loading.value = false
  }
})

watch(
  () => params.formulaRange,
  (newValue) => {
    if (formulaRangeModel.value !== newValue) {
      formulaRangeModel.value = newValue
    }
  }
)

watch(
  () => ({ mzPrecision: params.mzPrecision, formulaRange: params.formulaRange }),
  ({ mzPrecision, formulaRange }) => {
    if (mzPrecision != null && formulaRange && FORMULA_RANGE_PATTERN.test(formulaRange.trim())) {
      saveParams(mzPrecision, formulaRange)
    }
  }
)

watchEffect(() => {
  if (!chemConfig.value) return
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
    if (!chemConfig.value) return {}
    return {
      peakFocused: app.data.peak.focused ? app.data.peak.focused.mz : null,
      sampleId: app.data.sample.focusedId,
      mzPrecision: params.mzPrecision,
      formulaRange: params.formulaRange,
      ionMechanismIds: ionMechs.value.map((m) => m.ionization_mechanism_id).join(',')
    }
  },
  async (deps) => {
    if (!chemConfig.value || !deps.peakFocused || !deps.mzPrecision || !deps.formulaRange) {
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
        match_params: app.data.match.params.typeDefaults
      },
      {
        use: 'read',
        type: 'match_compositions_by_mz'
      }
    )
  },
  {
    debounce: computed(() => chemConfig.value?.DEBOUNCE_DELAY_MS ?? DEFAULT_DEBOUNCE_DELAY_MS),
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
    close: (Math.abs(record.mz - app.data.peak.focused?.mz) * 1e6) / record.mz < params.mzPrecision,
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
// from the other peak's fit score, and demotes the satellites of the formula
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
  <div class="search-pane" v-if="!embedded || app.data.peak.list.length > 0" v-help.top="rootHelp">
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
            </p>
          `)
        "
      >
        <InputNumber v-model="params.mzPrecision" id="mzPrecision" :min="1" :max="100" fluid />
        <label for="mzPrecision">m/z precision</label>
      </FloatLabel>
      <FloatLabel
        style="flex-grow: 1"
        :pt="
          app.ui.help.bottom(`
            <h1>Formula Range</h1>
            <p>
            Allowed element counts for candidate formulas, as space-separated
            ranges &mdash; e.g. <code>C0-100 H0-200 [15N]0-1</code>, isotopes in
            brackets. Narrowing the ranges makes the search faster and keeps
            chemically irrelevant candidates out.
            </p>
          `)
        "
      >
        <InputText
          v-model="formulaRangeModel"
          id="formulaRange"
          fluid
          :invalid="!isFormulaRangeValid"
          @blur="updateFormulaRange"
          @keydown.enter="updateFormulaRange"
          v-tooltip.bottom="{
            value: 'Format: Element + range, e.g. C0-100 H0-200 [15N]0-1 ^N0-1',
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
    </menu>
    <DataTable
      v-if="!loading && results.length > 0"
      :value="results"
      dataKey="target_compound_formula"
      :sortField="peakAssignmentEnabled ? 'fit_score' : 'match_score'"
      :sortOrder="-1"
      scrollable
      :scrollHeight="`${Math.max(120, height - 120)}px`"
      size="small"
      v-model:expandedRows="expanded"
      :virtualScrollerOptions="{ itemSize: 35.5 }"
      :pt="app.ui.help.top(resultsHelp)"
    >
      <Column expander />
      <Column field="target_compound_formula" header="Formula" sortable />
      <Column field="cheminfo.target_compound_unsaturation" sortable>
        <template #header>
          <span v-tooltip="{ value: 'Degree of unsaturation', showDelay: 500 }"><b>DBE</b></span>
        </template>
      </Column>
      <Column field="cheminfo.target_isotope_mz" header="Isotope m/z" sortable>
        <template #body="{ data }">
          {{ num.mz.format(data.cheminfo.target_isotope_mz) }}
        </template>
      </Column>
      <Column field="cheminfo.ionization_mechanism.ionization_mechanism" header="Mech." sortable />
      <Column field="cheminfo.target_isotope_mz_error_ppm" header="Error (ppm)" sortable>
        <template #body="{ data }">
          {{ num.mzError.format(data.cheminfo.target_isotope_mz_error_ppm) }}
        </template>
      </Column>
      <Column v-if="peakAssignmentEnabled" field="fit_score" sortable>
        <template #header>
          <span
            class="pi ph ph-seal-check"
            v-tooltip="{ value: 'Fit score & confidence tier', showDelay: 500 }"
          />
        </template>
        <template #body="{ data }">
          <BaseTierTag :tier="data.tier" :evidence="data.evidence" :source="data.source" />
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
          {{ data.plausibility != null ? formatFit(data.plausibility) : '—' }}
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
            v-if="data.existing.length > 0"
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
          <div class="row-actions">
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
      <template #expansion="{ data }">
        <DataTable
          :value="getIsotopeRows(data)"
          dataKey="mz"
          selectionMode="single"
          v-model:selection="preview.peak"
          sortField="mz"
          size="small"
          style="margin-left: 3rem; margin-right: 10rem"
        >
          <Column field="close" sortable>
            <template #header>
              <span class="pi pi-info-circle" v-tooltip.left="'Peak info'" />
            </template>
            <template #body="{ data }">
              <span
                class="pi ph ph-crosshair"
                v-if="data.close"
                v-tooltip.left="'Within tolerance of searched peak'"
              />
            </template>
          </Column>
          <Column field="relative_abundance" header="Rel. Abu." sortable>
            <template #body="{ data }">
              {{ num.relativeAbundance.format(data.relative_abundance) }}
            </template>
          </Column>
          <Column field="mz" header="Isotope m/z" sortable>
            <template #body="{ data }">
              {{ num.mz.format(data.mz) }}
            </template>
          </Column>
          <Column field="match_mz_error" header="Error (ppm)" sortable>
            <template #body="{ data }">
              {{ num.mzError.format(data.match_mz_error) }}
            </template>
          </Column>
          <Column field="match_score" sortable>
            <template #header>
              <span class="pi ph ph-seal-percent" v-tooltip="'Match score'" />
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
        </DataTable>
      </template>
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
