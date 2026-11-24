<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import Button from 'primevue/button'
import FloatLabel from 'primevue/floatlabel'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import ToggleSwitch from 'primevue/toggleswitch'

import { useApp } from '@/stores'
import {
  AUTO_PRESET,
  PARAM_KEYS,
  fetchProfilePreview,
  isFormulaRange,
  usePeakAssignParams
} from '@/lib/peakAssignParams'
import {
  chemistryLabel,
  contextName,
  polarityMismatches,
  polarityWord,
  profileName
} from '@/lib/peakAssignProfiles'

// The peak-assignment run configuration, shared by the per-sample launcher and
// the batch's Edit assignment settings dialog so both offer the same knobs and
// the same bounds.
//
// Defaults and bounds come from the shared parameter store: the limits are the
// constants PeakAssignmentConfig validates against, so an input can never offer
// a value the API would reject. The values are the caller's `model`: the
// batch's settings, or a launch seeded from them
// (docs/dev/batch_assignment_one_action.md 3.4). The caller reads its model on
// save or launch, after `commit()`.

const props = defineProps({
  // Fields the caller has decided for the user and does not show: a launcher
  // that IS the untargeted stage pins `run_untargeted` on and hides its switch.
  // A hidden field is never bound, so nothing typed here changes it and the
  // reset control below leaves it alone.
  hidden: {
    type: Array,
    default: () => []
  },
  // What the launch is for, so the form can say what `auto` resolves to there:
  // the sample a per-sample run assigns, or the batch whose samples the batch
  // search reaches. With neither, the chemistry is named when the run starts.
  sampleItemId: {
    type: String,
    default: null
  },
  sampleBatchId: {
    type: String,
    default: null
  },
  // The values to bind: a batch's settings, or a launch seeded from them. The
  // caller owns it and reads it on save or launch. Read once, at setup: a
  // caller reseeds the same object rather than handing a new one.
  model: {
    type: Object,
    required: true
  },
  // Show the values without letting them change: a viewer's look at settings
  // only an editor may save. Said once above the fields, with the reason.
  readonly: {
    type: Boolean,
    default: false
  },
  readonlyReason: {
    type: String,
    default: null
  }
})

const app = useApp()
const store = usePeakAssignParams()
const params = props.model

onMounted(() => store.ensureLoaded())

// Help cards in this form live on the launcher dialogs' shared help layer:
// both hosts call app.ui.help.set('dialog_peak_assign') while their dialog is
// open, so the cards show only there and eclipse the dashboard's own cards.
const layer = 'dialog_peak_assign'
const vHelpLayer = app.ui.help.directive(layer)
const stagesDoc = app.ui.help.docUrl('how-it-works/peak-assignment/#the-two-stages')

// Reset clears exactly the fields this form shows. A hidden field was never the
// user's to set here, so it is not this control's to clear either.
const resettable = computed(() => PARAM_KEYS.filter((key) => !props.hidden.includes(key)))
const blankToNull = (value) => (value === '' || value === undefined ? null : value)
const atDefaults = computed(() => {
  if (!store.defaults) return true
  return resettable.value.every(
    (key) => blankToNull(params[key]) === blankToNull(store.defaults[key])
  )
})

// The bound model's fields back to the served defaults.
function resetFields() {
  for (const key of resettable.value) params[key] = store.defaults ? store.defaults[key] : null
}

// What the reset control resets.
const resetHelp = `
    <h1>Reset to Defaults</h1>
    <p>
    Puts every field above back to the value Mascope ships. Nothing is
    written until you save or launch.
    </p>`

// The untargeted fields wait on the switch, where the form shows one; a form
// that hides it is for a pass that is the untargeted search, so they are live.
const untargetedOff = computed(
  () => !props.hidden.includes('run_untargeted') && !params.run_untargeted
)

// The range is committed to the model only once it parses, so a half-typed
// range is never saved or launched with. The raw text lives here meanwhile;
// the same two-model split the search pane uses.
const formulaRangeModel = ref(params.formula_ranges ?? '')
const formulaRangeValid = computed(
  () => !formulaRangeModel.value || isFormulaRange(formulaRangeModel.value)
)

watch(
  () => params.formula_ranges,
  (value) => {
    if (value != null && formulaRangeModel.value !== value) formulaRangeModel.value = value
    // A model reseeded or reset to an empty range shows the field empty
    // rather than keeping the last text.
    else if (value == null) formulaRangeModel.value = ''
  }
)

function commitFormulaRange() {
  if (formulaRangeValid.value && formulaRangeModel.value) {
    params.formula_ranges = formulaRangeModel.value.trim()
  } else if (!formulaRangeModel.value) {
    // Cleared: the range follows the chemistry profile again.
    params.formula_ranges = null
  }
}

/**
 * Commit the range typed but not yet committed, and say whether the form can
 * be saved: a caller's Save or Assign may be pressed with the range field
 * still focused.
 *
 * @returns {boolean} False while the typed range does not parse.
 */
function commit() {
  commitFormulaRange()
  return formulaRangeValid.value
}

defineExpose({ commit })

// --- Chemistry ---------------------------------------------------------------
// The reagent profile says how the sample was ionized, the context what was
// sampled. Together they set the reagent pre-pass's cluster library, the
// untargeted stage's element grid and m/z window, and the ceiling a reference
// list is matched under, so they apply whether or not the untargeted stage
// runs. `auto` is the default: the profile is read off the sample's ionization
// mechanisms, and the context is the profile's own.
//
// A null value is a store that has not heard from /params yet, which a launch
// sends as nothing and the server reads as `auto`, so the selector says so -
// and the question below is asked once rather than again when /params lands.
const profileModel = computed({
  get: () => params.profile ?? AUTO_PRESET,
  set: (value) => (params.profile = value)
})
const contextModel = computed({
  get: () => params.context ?? AUTO_PRESET,
  set: (value) => (params.context = value)
})

// What that means for this launch is asked of the server, for the sample or the
// batch the launch is for, and asked again whenever either name changes. A
// batch can hold more than one ionization mode, so the answer is a list.
const preview = ref(null)
const previewFailed = ref(false)
let previewRequest = 0

watch(
  () => [props.sampleItemId, props.sampleBatchId, profileModel.value, contextModel.value],
  async ([sampleItemId, sampleBatchId, profile, context]) => {
    const request = ++previewRequest
    if (!sampleItemId && !sampleBatchId) {
      preview.value = null
      previewFailed.value = false
      return
    }
    try {
      const records = await fetchProfilePreview(
        { sampleItemId, sampleBatchId },
        { profile, context }
      )
      // A slower answer to an earlier question must not replace a later one.
      if (request !== previewRequest) return
      preview.value = records
      previewFailed.value = false
    } catch {
      if (request !== previewRequest) return
      preview.value = null
      previewFailed.value = true
    }
  },
  { immediate: true }
)

const profileIsAuto = computed(() => profileModel.value === AUTO_PRESET)
const contextIsAuto = computed(() => contextModel.value === AUTO_PRESET)
const forBatch = computed(() => !props.sampleItemId && Boolean(props.sampleBatchId))

// The one answer, when the launch has one: always for a sample, and for a batch
// whose samples all resolve alike.
const single = computed(() => (preview.value?.length === 1 ? preview.value[0] : null))
const distinct = (key) => new Set((preview.value ?? []).map((record) => record[key])).size
// One field's answer, where every sample the launch reaches shares it - a batch
// of two instrument classes searches one grid in two windows.
const shared = (key) => (preview.value?.length && distinct(key) === 1 ? preview.value[0] : null)

// `auto` names its answer in the option itself, so the closed selector reads as
// what the run will do rather than as a mode.
function autoLabel(named, several) {
  if (named) return `Auto (${named})`
  return several ? `Auto (${several})` : 'Auto'
}
const profileOptions = computed(() => [
  {
    value: AUTO_PRESET,
    label: autoLabel(
      profileIsAuto.value && shared('profile') ? profileName(shared('profile')) : null,
      profileIsAuto.value && distinct('profile') > 1 ? 'per sample' : null
    )
  },
  ...store.presets.profiles.map((preset) => ({
    value: preset.name,
    label: profileName({ profile: preset.name, profile_label: preset.label })
  }))
])
const contextOptions = computed(() => [
  {
    value: AUTO_PRESET,
    label: autoLabel(
      contextIsAuto.value && shared('context') ? contextName(shared('context')) : null,
      contextIsAuto.value && distinct('context') > 1 ? "each profile's own" : null
    )
  },
  ...store.presets.contexts.map((preset) => ({
    value: preset.name,
    label: contextName({ context: preset.name, context_label: preset.label }),
    description: preset.description
  }))
])

// Where the answer came from, said once beside it.
// On Auto the context is the one the samples' batch or dataset names, where
// either names one (step 3.6), and the profile's own otherwise.
const NAMED_BY = {
  batch: 'The context is the one the batch names.',
  dataset: 'The context is the one the dataset names.'
}
const contextFrom = computed(() => {
  if (!contextIsAuto.value) return null
  const named = NAMED_BY[shared('context_source')?.context_source]
  if (named) return named
  return profileIsAuto.value ? null : "The context is the profile's own."
})
const resolvedFrom = computed(() => {
  const said = []
  if (profileIsAuto.value) {
    said.push(
      forBatch.value
        ? "Read off each sample's ionization mechanisms."
        : "Read off the sample's ionization mechanisms."
    )
  }
  if (contextFrom.value) said.push(contextFrom.value)
  return said.length ? said.join(' ') : null
})

const samplesText = (count) => `${count} sample${count === 1 ? '' : 's'}`

// A named profile is applied as named: over samples of the other polarity it
// searches reagent chemistry they were never measured with. Said rather than
// refused - the server runs what it is asked to - because the name is saved on
// the batch and may have been chosen before samples of the other mode joined.
const mismatchText = computed(() => {
  const mismatched = polarityMismatches(preview.value)
  if (!mismatched.length) return null
  const record = mismatched[0]
  const profile = profileName(record)
  const own = polarityWord(record.profile_polarity)
  const theirs = polarityWord(record.polarity)
  if (!forBatch.value) {
    return `${profile} is a ${own}-mode profile, and this sample is ${theirs}.`
  }
  const count = mismatched.reduce((sum, entry) => sum + (entry.samples ?? 0), 0)
  const verb = count === 1 ? 'is' : 'are'
  return `${profile} is a ${own}-mode profile, and ${samplesText(count)} of this batch ${verb} ${theirs}.`
})

// The grid and the window the untargeted stage would search at, where every
// sample shares them: what leaving either field empty means, shown where it is
// left empty.
const fromProfile = () =>
  preview.value?.length > 1 ? "From each sample's chemistry profile" : 'From the chemistry profile'
const formulaRangePlaceholder = computed(
  () => shared('element_ranges')?.element_ranges ?? fromProfile()
)
// The profile's grid, offered as a value to start from: a click writes it into
// the field and commits it as a typed range is, and from then on it is the
// user's to edit. Emptied again, the field follows the profile once more. It
// is offered only where one grid is known for the whole launch, and the
// disabled button says why there is none.
const profileGrid = computed(() => shared('element_ranges')?.element_ranges ?? null)
const profileGridReason = computed(() => {
  if (untargetedOff.value) return 'Turn on Untargeted search to set a formula range.'
  if (profileGrid.value) return "Fill the field with the chemistry profile's grid, to edit it."
  if (distinct('element_ranges') > 1) return "The samples' chemistry profiles give different grids."
  return 'The chemistry profile is not read yet.'
})
function useProfileGrid() {
  if (!profileGrid.value) return
  formulaRangeModel.value = profileGrid.value
  commitFormulaRange()
}
const mzPrecisionPlaceholder = computed(() => {
  const ppm = shared('mz_precision_ppm')?.mz_precision_ppm
  return ppm != null ? String(ppm) : fromProfile()
})

const chemistryDoc = app.ui.help.docUrl(
  'how-it-works/peak-assignment/#the-chemistry-a-run-searches-under'
)
</script>

<template>
  <div class="col config-form" style="gap: 1.25rem; align-items: stretch">
    <Message
      v-if="readonly && readonlyReason"
      severity="secondary"
      size="small"
      data-testid="config-readonly"
    >
      {{ readonlyReason }}
    </Message>
    <FloatLabel
      :pt="
        app.ui.help.right(
          `
          <h1>Chemistry Profile</h1>
          <p>
          How the sample was ionized: the reagent chemistry that decides which
          cluster ions the source makes of itself, and which elements the
          sample's compounds can be built from. It sets the untargeted stage's
          element grid and m/z window, and the reagent ions set aside before
          either stage runs.
          </p>
          <p>
          <b>Auto</b> reads it off the sample's ionization mechanisms &mdash; a
          mode carrying the bromide mechanism is a bromide source, whatever it
          is called. <b>No profile</b> switches the layer off and searches the
          engine's original wide grid.
          </p>`,
          { layer, doc: chemistryDoc }
        )
      "
    >
      <Select
        v-model="profileModel"
        inputId="assign_profile"
        :options="profileOptions"
        optionLabel="label"
        optionValue="value"
        :disabled="readonly"
        fluid
      />
      <label for="assign_profile">Chemistry profile</label>
    </FloatLabel>
    <FloatLabel
      :pt="
        app.ui.help.right(
          `
          <h1>Chemistry Context</h1>
          <p>
          What was sampled: ambient air, a chamber, water, food. The context
          narrows the profile's element grid, rejects formulas whose hydrogen,
          oxygen, nitrogen or ring-and-double-bond count per carbon no such
          sample holds, and caps the window a reference list is matched in. It
          never widens the profile's grid.
          </p>
          <p>
          <b>Auto</b> takes the context the sample's batch or dataset names,
          and otherwise the one the profile is normally used with.
          <b>No context</b> applies no such prior.
          </p>`,
          { layer, doc: chemistryDoc }
        )
      "
    >
      <Select
        v-model="contextModel"
        inputId="assign_context"
        :options="contextOptions"
        optionLabel="label"
        optionValue="value"
        :disabled="readonly"
        fluid
      >
        <template #option="{ option }">
          <span v-tooltip.right="option.description || null">{{ option.label }}</span>
        </template>
      </Select>
      <label for="assign_context">Chemistry context</label>
    </FloatLabel>
    <div
      v-if="preview?.length || previewFailed"
      class="chemistry-resolved"
      data-testid="chemistry-resolved"
    >
      <template v-if="single">
        <span>
          {{ forBatch ? `All ${samplesText(single.samples)} search as` : 'Searches as' }}
          <b>{{ chemistryLabel(single) }}</b>
        </span>
        <small v-if="resolvedFrom">{{ resolvedFrom }}</small>
      </template>
      <template v-else-if="preview?.length">
        <span>Each sample searches under its own chemistry:</span>
        <ul>
          <li
            v-for="record in preview"
            :key="`${record.profile}|${record.context}|${record.polarity}|${record.mz_precision_ppm}`"
          >
            <b>{{ chemistryLabel(record) }}</b>
            <template v-if="distinct('mz_precision_ppm') > 1">
              &middot; {{ record.mz_precision_ppm }} ppm</template
            >
            &middot; {{ samplesText(record.samples) }}
          </li>
        </ul>
        <small v-if="resolvedFrom">{{ resolvedFrom }}</small>
      </template>
      <small v-else>Could not look the chemistry up. The run resolves it when it starts.</small>
    </div>
    <Message
      v-if="mismatchText"
      severity="warn"
      size="small"
      variant="simple"
      data-testid="chemistry-polarity"
    >
      {{ mismatchText }}
    </Message>
    <div
      v-if="!hidden.includes('run_untargeted')"
      class="toggle-row"
      v-help-layer.right="{
        message: `
          <h1>Untargeted Search</h1>
          <p>
          Assignment always matches peaks against the sample's known target
          library first. This switch adds the untargeted stage: a bounded
          composition search over the peaks the library leaves unexplained. It
          finds unknowns, and it is the slowest part of a run.
          </p>`,
        doc: stagesDoc
      }"
    >
      <ToggleSwitch v-model="params.run_untargeted" inputId="run_untargeted" :disabled="readonly" />
      <label for="run_untargeted">
        Untargeted search
        <small>Search compositions for peaks the library leaves unassigned.</small>
      </label>
    </div>
    <FloatLabel
      :pt="
        app.ui.help.right(
          `
          <h1>m/z Precision</h1>
          <p>
          The mass tolerance of the untargeted stage, in ppm: candidate formulas
          must land within this window of the peak. Widening it finds more
          candidates, but slower and more ambiguous ones. The library stage is
          unaffected &mdash; it uses the sample's match parameters.
          </p>
          <p>
          Left empty, the run uses the window its chemistry profile implies for
          the instrument &mdash; an Orbitrap assigns far inside 10&nbsp;ppm, a
          TOF needs more room.
          </p>`,
          { layer, doc: stagesDoc }
        )
      "
    >
      <InputNumber
        v-model="params.mz_precision_ppm"
        inputId="mz_precision_ppm"
        :min="1"
        :max="store.limits.max_mz_precision_ppm"
        :disabled="readonly || untargetedOff"
        :placeholder="mzPrecisionPlaceholder"
        fluid
      />
      <label for="mz_precision_ppm">m/z precision (ppm)</label>
    </FloatLabel>
    <div class="field-row">
      <FloatLabel
        :pt="
          app.ui.help.right(
            `
          <h1>Formula Range</h1>
          <p>
          Element-count bounds for untargeted candidate formulas, as
          space-separated ranges &mdash; e.g.
          <code>C0-80 H0-160 O0-50 N0-20</code>, isotopes in brackets
          (<code>[15N]0-1</code>). At most 12 element species; every added
          element multiplies the search space.
          </p>
          <p>
          Left empty, the run uses the grid its chemistry profile implies
          &mdash; the elements the sample's reagent chemistry can actually
          produce, which is both a narrower and a faster search.
          <b>Use profile grid</b> fills the field with that grid, for you to
          edit; empty the field again to follow the profile.
          </p>`,
            { layer, doc: stagesDoc }
          )
        "
      >
        <InputText
          v-model="formulaRangeModel"
          id="formula_ranges"
          :disabled="readonly || untargetedOff"
          :invalid="!formulaRangeValid"
          @blur="commitFormulaRange"
          @keydown.enter="commitFormulaRange"
          v-tooltip.bottom="{
            value: 'Format: Element + range, e.g. C0-80 H0-160 [15N]0-1 ^N0-1',
            showDelay: 500
          }"
          :placeholder="formulaRangePlaceholder"
          fluid
        />
        <label for="formula_ranges">Formula range</label>
      </FloatLabel>
      <!-- A disabled button takes no hover, so the reason sits on its wrapper. -->
      <span
        v-if="!readonly"
        class="profile-grid"
        v-tooltip.bottom="profileGridReason"
        data-testid="profile-grid"
      >
        <Button
          label="Use profile grid"
          icon="pi ph ph-grid-four"
          size="small"
          text
          severity="secondary"
          :disabled="untargetedOff || !profileGrid"
          @click="useProfileGrid"
        />
      </span>
    </div>
    <FloatLabel
      :pt="
        app.ui.help.right(
          `
          <h1>Max Untargeted Peaks</h1>
          <p>
          At most this many peaks enter the untargeted stage: the most intense
          of the peaks the library left unexplained, after the intensity
          threshold.
          </p>
          <p>
          Left empty, every unexplained peak is searched. Set it only to cut a
          run short &mdash; a peak nobody searched looks exactly like a peak
          nothing could explain.
          </p>`,
          { layer, doc: stagesDoc }
        )
      "
    >
      <InputNumber
        v-model="params.max_untargeted_peaks"
        inputId="max_untargeted_peaks"
        :min="1"
        :max="store.limits.max_untargeted_peaks_ceiling"
        :disabled="readonly || untargetedOff"
        placeholder="Every unexplained peak"
        fluid
      />
      <label for="max_untargeted_peaks">Max untargeted peaks</label>
    </FloatLabel>
    <FloatLabel
      :pt="
        app.ui.help.right(
          `
          <h1>Peak Intensity Threshold</h1>
          <p>
          Only unexplained peaks at least this intense (in the sample's native
          intensity units) enter the untargeted stage. Zero searches everything
          the peak cap allows.
          </p>`,
          { layer, doc: stagesDoc }
        )
      "
    >
      <InputNumber
        v-model="params.peak_intensity_threshold"
        inputId="peak_intensity_threshold"
        :min="0"
        :disabled="readonly || untargetedOff"
        fluid
      />
      <label for="peak_intensity_threshold">Peak intensity threshold</label>
    </FloatLabel>
    <FloatLabel
      :pt="
        app.ui.help.right(
          `
          <h1>Max Alternatives Kept</h1>
          <p>
          How many runner-up candidates are stored per peak, from both stages.
          They appear as the close alternatives in the peak inspector.
          </p>`,
          {
            layer,
            doc: app.ui.help.docUrl(
              'how-it-works/peak-assignment/#arbitration-competing-the-candidates'
            )
          }
        )
      "
    >
      <InputNumber
        v-model="params.max_alternatives"
        inputId="max_alternatives"
        :min="0"
        :max="store.limits.max_alternatives_ceiling"
        :disabled="readonly"
        fluid
      />
      <label for="max_alternatives">Max alternatives kept</label>
    </FloatLabel>
    <!-- A batch's settings are saved and shape every later pass, so the way
         back to the shipped defaults has to be visible: without it a range
         narrowed once would quietly stay. Disabled while every field is on its
         default, which also makes the control double as the answer to "am I
         on the defaults?". -->
    <div v-if="!readonly" class="reset-row">
      <Button
        label="Reset to defaults"
        icon="pi ph ph-arrow-counter-clockwise"
        size="small"
        text
        severity="secondary"
        :disabled="atDefaults"
        @click="resetFields"
        v-help-layer.right="{
          message: resetHelp
        }"
      />
    </div>
  </div>
</template>

<style scoped>
.config-form :deep(small) {
  display: block;
  opacity: 0.7;
}

/* The answer sits under the two selectors it answers, in the recessive voice
   the form's other asides use. */
.chemistry-resolved {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  margin-top: -0.5rem;
  font-size: 0.85rem;
}

.chemistry-resolved ul {
  margin: 0;
  padding-left: 1.1rem;
}

/* The profile-grid button trails the field it fills, as the reset control
   trails the form: the field keeps the dialog's full width. */
.field-row {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.profile-grid {
  align-self: flex-end;
}

.toggle-row {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
}

/* Trails the fields it acts on rather than sharing the dialog footer: the
   footer is where the run is launched from, and a destructive-ish reset beside
   the primary action is a misclick waiting to happen. */
.reset-row {
  display: flex;
  justify-content: flex-end;
  margin-top: -0.5rem;
}
</style>
