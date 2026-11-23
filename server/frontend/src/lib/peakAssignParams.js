import { computed, reactive, ref, watch } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'

/**
 * The untargeted search parameters: the served defaults, bounds and presets
 * every surface that asks for them reads, and one user's own remembered values.
 *
 * The remembered values are the composition search pane's (m/z precision and
 * formula range, for the focused peak): the server default until the user
 * changes it, the user's value afterwards, across reloads. An assignment run
 * does not read them. A batch keeps its own settings on itself, which every
 * pass of the batch runs on and which the per-sample launcher opens on
 * (docs/dev/batch_assignment_one_action.md 3.4, decided in MAS-1012); the
 * helpers below the store's constants turn those into a form model and back.
 * Before that, the launchers bound this record too, so the same settings
 * differed from one user to the next on one batch.
 *
 * Only genuine overrides are persisted: a field left at the server default is
 * absent from storage, so a default that moves in a release moves for everyone
 * who never touched it. That is the whole reason the record is keyed by field
 * rather than snapshotted - a stored copy of last release's defaults would pin
 * every user to them silently.
 */

// A new key, not the pane's old one: that held `{mzPrecision, formulaRange}`,
// the pane's own names for two of these fields, and it stored them whether or
// not they differed from the defaults. Dropping it is the migration - everyone
// starts from the current defaults, and anyone who had genuinely tuned the pane
// re-tunes it once.
const STORAGE_KEY = 'mascope.peakAssign.config'
const LEGACY_STORAGE_KEY = 'mascope.peakAssign.params'

// The fields this store owns: the run config the API accepts, minus what is
// decided per launch rather than by the user. Null means "no override yet" and
// is replaced by the server default the moment /params answers.
//
// The chemistry profile and context are the run's too, and persist like the
// rest: a launcher shows both on every open, so a named one is never carried
// invisibly, and the reset control puts both back on `auto`.
const BLANK = Object.freeze({
  run_untargeted: null,
  profile: null,
  context: null,
  mz_precision_ppm: null,
  formula_ranges: null,
  max_untargeted_peaks: null,
  peak_intensity_threshold: null,
  max_alternatives: null
})

export const PARAM_KEYS = Object.freeze(Object.keys(BLANK))

// Generous bounds used only until /params answers, at which point the real
// ceilings arrive - they are the constants PeakAssignmentConfig validates
// against, so an input can never offer a value the API would reject.
const FALLBACK_LIMITS = Object.freeze({
  max_untargeted_peaks_ceiling: 5000,
  max_mz_precision_ppm: 100,
  max_alternatives_ceiling: 50
})

// Fallback debounce for the pane's search, used until /params answers.
const FALLBACK_DEBOUNCE_MS = 800

/**
 * The `profile` and `context` value that asks the engine to work the chemistry
 * out from the sample - the server's default for both.
 */
export const AUTO_PRESET = 'auto'

// No presets until /params answers: a selector offers `auto` alone meanwhile,
// which is what a run would use anyway.
const NO_PRESETS = Object.freeze({ profiles: [], contexts: [] })

// The params key a preset list is served under, per field.
const PRESET_LISTS = Object.freeze({ profile: 'profiles', context: 'contexts' })

// "C0-100 H0-200 Cl0-10", isotopes in brackets ([15N]0-1) or caret form (^N0-1).
const ELEMENT_PATTERN = '(?:[A-Z][a-z]?|\\^[A-Z][a-z]?|\\[\\d*[A-Z][a-z]?\\])'
const RANGE_PATTERN = '\\d+-\\d+'
export const FORMULA_RANGE_PATTERN = new RegExp(
  `^(${ELEMENT_PATTERN}${RANGE_PATTERN})(\\s+${ELEMENT_PATTERN}${RANGE_PATTERN})*$`
)

/**
 * Whether a formula-range string is well formed.
 *
 * Shared rather than restated per surface: the range is now one persisted
 * value, so a string the pane would have rejected must not reach the store
 * through the launcher's field and come back as the pane's next search.
 *
 * @param {*} value - Candidate formula-range string.
 * @returns {boolean} True when the string parses as a list of element ranges.
 */
export function isFormulaRange(value) {
  return typeof value === 'string' && FORMULA_RANGE_PATTERN.test(value.trim())
}

/**
 * The preview endpoint for a launch scope.
 *
 * @param {{sampleItemId?: string|null, sampleBatchId?: string|null}} scope
 * @returns {string|null} the path, or null when the scope names nothing
 */
export function previewPath({ sampleItemId = null, sampleBatchId = null } = {}) {
  if (sampleItemId) return `/peak-assignments/sample/${sampleItemId}/profile-preview`
  if (sampleBatchId) return `/peak-assignments/batch/${sampleBatchId}/profile-preview`
  return null
}

/**
 * What a run config's profile and context resolve to, over a sample or a batch.
 *
 * Failures are the caller's to show: the preview sits beside a form as a
 * convenience, so a failed lookup is said there rather than toasted.
 *
 * @param {{sampleItemId?: string|null, sampleBatchId?: string|null}} scope
 * @param {{profile?: string|null, context?: string|null}} [names] - null reads
 *   as `auto`, which is what a launch that sends nothing gets
 * @returns {Promise<Array<object>>} one record per distinct resolution, the
 *   most samples first; empty for a scope that names nothing
 */
export async function fetchProfilePreview(scope, { profile = null, context = null } = {}) {
  const path = previewPath(scope)
  if (!path) return []
  const response = await api.http.get(path, {
    params: { profile: profile ?? 'auto', context: context ?? 'auto' },
    type: 'load_profile_preview',
    errors: 'inline'
  })
  return response?.data?.data ?? []
}

// --- A batch's settings ----------------------------------------------------
// A batch keeps its assignment settings on itself (`assignment_settings` on the
// batch, docs/dev/batch_assignment_one_action.md 5.4): every pass of its stages
// reads them, whoever starts it. The batch stores the fields that were set, so
// a field it leaves out follows the engine's default. The helpers below turn
// that record into a whole form model and back, for the batch's own dialog and
// for the per-sample launcher, which opens on the batch's settings.

/**
 * The fields a batch's settings dialog offers. The batch's pass IS the
 * untargeted search's stage, so it shows no switch for running it.
 */
export const BATCH_SETTING_KEYS = Object.freeze(
  PARAM_KEYS.filter((key) => key !== 'run_untargeted')
)

// A field left blank in a form is no value at all: the default applies.
const blank = (value) => value === null || value === undefined || value === ''

/**
 * A whole form model from a batch's saved settings: what the batch saved,
 * and the server default for every field it left out.
 *
 * @param {object|null} saved - The batch's `assignment_settings`; null is the defaults.
 * @param {object|null} defaults - The served defaults (`store.defaults`); null
 *   while /params has not answered, which leaves an unsaved field empty.
 * @returns {object} One value per `PARAM_KEYS` field.
 */
export function batchSettingsModel(saved, defaults) {
  const out = {}
  for (const key of PARAM_KEYS) {
    out[key] = saved && key in saved ? saved[key] : (defaults?.[key] ?? null)
  }
  return out
}

/**
 * What saving a batch's settings dialog writes: the record the batch keeps,
 * and whether it differs from the one it keeps now.
 *
 * A field the dialog left as it opened keeps what the batch saved, or stays
 * out where it saved nothing. A changed field is written, unless it was put
 * back on the server default or cleared: then it is left out, so it follows
 * the default as the per-user store's overrides do. A PATCH replaces the
 * batch's settings whole, so the fields the batch saved before and nobody
 * touched here travel with the changed ones.
 *
 * @param {object} model - The dialog's form model.
 * @param {object|null} saved - The batch's `assignment_settings` as it opened.
 * @param {object|null} defaults - The served defaults.
 * @param {string[]} [keys] - The fields the dialog shows.
 * @returns {{settings: object|null, changed: boolean}} `settings` is null for
 *   the defaults, as the batch stores them.
 */
export function batchSettingsBody(model, saved, defaults, keys = BATCH_SETTING_KEYS) {
  const opened = batchSettingsModel(saved, defaults)
  const out = {}
  // What the batch saved of a field this dialog does not show stays saved.
  for (const [key, value] of Object.entries(saved ?? {})) {
    if (!keys.includes(key)) out[key] = value
  }
  for (const key of keys) {
    const value = blank(model[key]) ? null : model[key]
    const before = blank(opened[key]) ? null : opened[key]
    if (value === before) {
      if (saved && key in saved) out[key] = saved[key]
      continue
    }
    if (value === null || value === (defaults?.[key] ?? null)) continue
    if (key === 'formula_ranges' && !isFormulaRange(value)) continue
    out[key] = value
  }
  const settings = Object.keys(out).length ? out : null
  const same = JSON.stringify(sortedEntries(settings)) === JSON.stringify(sortedEntries(saved))
  return { settings, changed: !same }
}

// A record's entries in key order, empty for none, so two records compare by
// content and an empty object reads as no settings, as the batch stores it.
function sortedEntries(record) {
  return Object.entries(record ?? {}).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
}

/**
 * The run configuration a form model launches with: the fields that are set,
 * as the per-user store's `payload` sends them, so the run records the
 * settings it ran on and the backend default applies to a field left blank.
 *
 * @param {object} model - A form model.
 * @param {object} [forced] - Fields the caller decides, applied over the model's.
 * @returns {object} Body `config` for the assignment endpoint.
 */
export function runConfig(model, forced = {}) {
  const out = {}
  for (const key of PARAM_KEYS) {
    const value = model[key]
    if (blank(value)) continue
    if (key === 'formula_ranges' && !isFormulaRange(value)) continue
    out[key] = value
  }
  return { ...out, ...forced }
}

/** Overrides held in storage, filtered to the fields this store still owns. */
function load() {
  try {
    localStorage.removeItem(LEGACY_STORAGE_KEY)
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null')
    if (!stored || typeof stored !== 'object') return {}
    const out = {}
    for (const key of PARAM_KEYS) {
      if (stored[key] === null || stored[key] === undefined) continue
      out[key] = stored[key]
    }
    // A range that no longer parses is dropped rather than restored: it would
    // fail every search it reached, and the field it lands in is the one place
    // the user could not tell it apart from a default.
    if ('formula_ranges' in out && !isFormulaRange(out.formula_ranges)) {
      delete out.formula_ranges
    }
    return out
  } catch {
    // Unreadable or absent storage: the defaults apply.
    return {}
  }
}

function save(overrides) {
  try {
    if (Object.keys(overrides).length === 0) localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides))
  } catch {
    // Storage refused (quota, private mode): the values still hold for the session.
  }
}

export const usePeakAssignParams = defineStore('peakAssign.params', () => {
  // Stored overrides over "not yet known", so a field the user never touched
  // stays null until the server default lands on it.
  const params = reactive({ ...BLANK, ...load() })
  const limits = ref({ ...FALLBACK_LIMITS })
  const debounceMs = ref(FALLBACK_DEBOUNCE_MS)
  const defaults = ref(null)
  // The chemistry profiles and contexts a run config may name, as served:
  // `{name, label, description, polarity, default_context}` each.
  const presets = ref(NO_PRESETS)

  /** Whether /params has answered. Until it does, unset fields are still null. */
  const loaded = computed(() => defaults.value !== null)

  /**
   * The fields that differ from the server default - all that is persisted.
   *
   * An invalid formula range is never written: the launcher's field binds
   * straight to the store, so a half-typed range would otherwise be saved on
   * its way to being finished.
   */
  const overrides = computed(() => {
    const out = {}
    if (!defaults.value) return out
    for (const key of PARAM_KEYS) {
      const value = params[key]
      if (value === null || value === undefined || value === '') continue
      if (value === defaults.value[key]) continue
      if (key === 'formula_ranges' && !isFormulaRange(value)) continue
      out[key] = value
    }
    return out
  })

  // Persist after the defaults land, never before: a write during the fetch
  // window would record nulls, and one before the defaults are known could not
  // tell an override from a value that merely equals the default.
  watch(overrides, (next) => {
    if (!loaded.value) return
    save(next)
  })

  // One request for every surface that mounts: the launcher dialog, the batch
  // dialog and the search pane all want the same three answers, and two of them
  // can be on screen at once.
  let pending = null

  /**
   * Fetch the server defaults, bounds and debounce once per session.
   *
   * @returns {Promise<void>} Resolves when /params has been applied (or failed).
   */
  function ensureLoaded() {
    if (pending) return pending
    pending = api.http
      .get('/params', { type: 'read_params' })
      .then(({ data }) => {
        const served = data?.data?.params
        if (served?.peak_assignment_limits) limits.value = served.peak_assignment_limits
        const delay = served?.cheminfo_config?.DEBOUNCE_DELAY_MS
        if (typeof delay === 'number') debounceMs.value = delay
        const servedPresets = served?.peak_assignment_presets
        if (servedPresets) {
          presets.value = Object.freeze({
            profiles: servedPresets.profiles ?? [],
            contexts: servedPresets.contexts ?? []
          })
        }
        const servedDefaults = served?.peak_assignment
        if (!servedDefaults) return
        defaults.value = Object.freeze(
          Object.fromEntries(PARAM_KEYS.map((key) => [key, servedDefaults[key] ?? null]))
        )
        // Fill only what the user has not overridden, so the defaults arriving
        // late cannot overwrite a value already restored or typed.
        for (const key of PARAM_KEYS) {
          if (params[key] === null || params[key] === undefined) {
            params[key] = defaults.value[key]
          }
        }
        // A stored name this server no longer offers goes back to the default,
        // as an unparseable range is dropped on load: the API would refuse the
        // launch it reached, and a selector could not show it.
        for (const [key, list] of Object.entries(PRESET_LISTS)) {
          const offered = presets.value[list]
          const value = params[key]
          if (!offered.length || value === AUTO_PRESET || value === defaults.value[key]) continue
          if (!offered.some((preset) => preset.name === value)) params[key] = defaults.value[key]
        }
      })
      .catch(() => {
        // Bounds and defaults are a convenience; the API validates regardless.
        // Cleared so a later mount can try again.
        pending = null
      })
    return pending
  }

  /**
   * The run configuration to launch with.
   *
   * Only fields that are actually set: anything still unknown - the dialog was
   * opened before /params answered - is left out so the backend default applies
   * rather than a null overriding it. An unparseable formula range is dropped
   * on the same grounds; it would only be rejected downstream.
   *
   * @param {object} [forced] - Fields the caller decides for itself, applied
   *   over the user's. The batch search IS the untargeted stage, so it forces
   *   `run_untargeted` on rather than reading a switch it does not show.
   * @returns {object} Body for the assignment or untargeted-search endpoint.
   */
  function payload(forced = {}) {
    const out = {}
    for (const key of PARAM_KEYS) {
      const value = params[key]
      if (value === null || value === undefined || value === '') continue
      if (key === 'formula_ranges' && !isFormulaRange(value)) continue
      out[key] = value
    }
    return { ...out, ...forced }
  }

  /**
   * Whether the given fields are all at the server default.
   *
   * @param {string[]} [keys] - Fields to test; defaults to all of them.
   * @returns {boolean} True when none of the fields is overridden.
   */
  function isDefault(keys = PARAM_KEYS) {
    return keys.every((key) => !(key in overrides.value))
  }

  /**
   * Put the given fields back to the server defaults.
   *
   * Scoped rather than global on purpose: a surface's reset control clears
   * exactly the fields that surface shows, so resetting from the search pane
   * cannot silently discard a peak ceiling set in the launcher dialog.
   *
   * @param {string[]} [keys] - Fields to reset; defaults to all of them.
   */
  function reset(keys = PARAM_KEYS) {
    for (const key of keys) {
      if (!PARAM_KEYS.includes(key)) continue
      params[key] = defaults.value ? defaults.value[key] : null
    }
  }

  return {
    params,
    limits,
    debounceMs,
    defaults,
    presets,
    loaded,
    overrides,
    ensureLoaded,
    payload,
    isDefault,
    reset
  }
})
