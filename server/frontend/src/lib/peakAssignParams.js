import { computed, reactive, ref, watch } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'

/**
 * The untargeted search parameters, shared by every surface that asks for them.
 *
 * Three controls take the same knobs: the composition search pane (m/z
 * precision and formula range, for the focused peak), the per-sample assignment
 * launcher, and the batch untargeted search. They used to disagree about where
 * a value comes from - the pane restored the user's last two values from
 * localStorage, while the launcher form reset to the server defaults on every
 * open - so the same parameter had two answers depending on which control you
 * opened, and a range narrowed in the pane was gone the moment you launched a
 * run with it.
 *
 * Here it has one answer: the server default until the user changes it, the
 * user's value afterwards, on every surface and across reloads. That is one
 * record deliberately, not three: m/z precision and formula range mean the same
 * thing to the pane and to the run - the backend already derives both from the
 * same two constants (`ChemInfoConfig`) - so tuning them against one peak in
 * the pane is what the next run launches with. Every surface that binds a field
 * also shows it, so nothing is carried invisibly.
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
