import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

// The untargeted-search parameters: the served defaults, bounds and presets
// every surface reads, the composition search pane's two remembered fields,
// and the helpers that turn a batch's saved settings into a form model and a
// run's request.
//
// What is worth pinning is not that a value round-trips but WHICH values are
// written: only genuine overrides, so a default that moves in a release moves
// with it for everyone who never touched the knob. Storing the whole record
// would silently freeze every user on the defaults of the release they first
// opened the dialog in - which is exactly what the pane's old storage key did,
// and why a shipped default change would not have reached them.

const SERVED = {
  run_untargeted: true,
  profile: 'auto',
  context: 'auto',
  mz_precision_ppm: 3,
  formula_ranges: 'C0-80 H0-160 O0-50 N0-20',
  max_untargeted_peaks: 300,
  peak_intensity_threshold: 0,
  max_alternatives: 5
}

const LIMITS = {
  max_untargeted_peaks_ceiling: 2000,
  max_mz_precision_ppm: 50,
  max_alternatives_ceiling: 20
}

const PRESETS = {
  profiles: [
    { name: 'BR', label: 'Bromide CIMS', polarity: '-', default_context: 'ambient-air' },
    { name: 'none', label: 'None', polarity: '', default_context: 'none' }
  ],
  contexts: [
    { name: 'ambient-air', label: 'Ambient air', description: 'Outdoor air.' },
    { name: 'none', label: 'None', description: 'No matrix prior.' }
  ]
}

const get = vi.fn()
vi.mock('@/api', () => ({ api: { http: { get: (...args) => get(...args) } } }))

// Imported after the mock (vi.mock is hoisted above it).
const {
  usePeakAssignParams,
  isFormulaRange,
  PARAM_KEYS,
  BATCH_SETTING_KEYS,
  SEARCH_KEYS,
  batchSettingsBody,
  batchSettingsModel,
  runConfig
} = await import('@/lib/peakAssignParams')

const STORAGE_KEY = 'mascope.peakAssign.config'
const LEGACY_STORAGE_KEY = 'mascope.peakAssign.params'

/** What /params answers with, unless a test says otherwise. */
function served({ peak_assignment = SERVED, ...rest } = {}) {
  return {
    data: {
      data: {
        params: {
          peak_assignment,
          peak_assignment_limits: LIMITS,
          peak_assignment_presets: PRESETS,
          cheminfo_config: { DEBOUNCE_DELAY_MS: 250 },
          ...rest
        }
      }
    }
  }
}

// The served defaults of the fields the store remembers.
const SEARCH_DEFAULTS = {
  mz_precision_ppm: SERVED.mz_precision_ppm,
  formula_ranges: SERVED.formula_ranges
}

const stored = () => JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null')

/** A loaded store: the defaults have landed, as they have by the time a form shows. */
async function loadedStore() {
  const store = usePeakAssignParams()
  await store.ensureLoaded()
  return store
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  get.mockReset()
  get.mockResolvedValue(served())
})

describe('peak assignment parameters: defaults', () => {
  it('starts every field on the server default', async () => {
    const store = await loadedStore()
    expect({ ...store.params }).toEqual(SEARCH_DEFAULTS)
    expect(store.defaults).toEqual(SERVED)
    expect(store.loaded).toBe(true)
    expect(store.isDefault()).toBe(true)
  })

  it('takes the bounds and the search debounce from the same answer', async () => {
    const store = await loadedStore()
    expect(store.limits).toEqual(LIMITS)
    expect(store.debounceMs).toBe(250)
  })

  it('takes the chemistry presets from the same answer', async () => {
    const store = usePeakAssignParams()
    // Until /params answers there is nothing to offer beside auto.
    expect(store.presets).toEqual({ profiles: [], contexts: [] })
    await store.ensureLoaded()
    expect(store.presets).toEqual(PRESETS)
  })

  it('fetches once however many surfaces ask', async () => {
    const store = usePeakAssignParams()
    await Promise.all([store.ensureLoaded(), store.ensureLoaded(), store.ensureLoaded()])
    expect(get).toHaveBeenCalledTimes(1)
  })

  it('leaves the fields unset when /params cannot be reached, and retries later', async () => {
    get.mockRejectedValueOnce(new Error('offline'))
    const store = usePeakAssignParams()
    await store.ensureLoaded()
    expect(store.loaded).toBe(false)
    expect(store.params.mz_precision_ppm).toBeNull()

    // A later mount gets a fresh attempt rather than the failed promise.
    await store.ensureLoaded()
    expect(store.loaded).toBe(true)
    expect(store.params.mz_precision_ppm).toBe(3)
  })
})

describe('peak assignment parameters: persistence', () => {
  it('stores only what differs from the default', async () => {
    const store = await loadedStore()
    store.params.mz_precision_ppm = 8
    await nextTick()

    expect(stored()).toEqual({ mz_precision_ppm: 8 })
    expect(store.isDefault()).toBe(false)
  })

  it('drops a field from storage again once it is back on the default', async () => {
    const store = await loadedStore()
    store.params.mz_precision_ppm = 9
    await nextTick()
    expect(stored()).toEqual({ mz_precision_ppm: 9 })

    store.params.mz_precision_ppm = SERVED.mz_precision_ppm
    await nextTick()
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('restores an override on the next session and follows the default elsewhere', async () => {
    // The junk field stands for a name this store no longer owns: a record
    // written by another version must not smuggle one back into the params.
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ mz_precision_ppm: 8, retired_knob: 'nonsense' })
    )
    // A release moves the defaults under the user.
    get.mockResolvedValue(served({ peak_assignment: { ...SERVED, formula_ranges: 'C0-10 H0-20' } }))
    const store = await loadedStore()

    expect(store.params.mz_precision_ppm).toBe(8) // theirs, kept
    expect(store.params.formula_ranges).toBe('C0-10 H0-20') // the new default, followed
    expect(Object.keys(store.params).sort()).toEqual([...SEARCH_KEYS].sort())
  })

  it("drops what the launchers once stored: a run's settings are the batch's", async () => {
    // Before MAS-1017 the launchers bound this record, so a profile, a context
    // and the run's ceilings could sit in it. No control shows them now, so
    // none may be restored, and the next write leaves them out.
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ profile: 'BR', context: 'none', max_alternatives: 9, mz_precision_ppm: 8 })
    )
    const store = await loadedStore()

    expect({ ...store.params }).toEqual({ ...SEARCH_DEFAULTS, mz_precision_ppm: 8 })
    store.params.mz_precision_ppm = 7
    await nextTick()
    expect(stored()).toEqual({ mz_precision_ppm: 7 })
  })

  it('never writes before the defaults have landed', async () => {
    const store = usePeakAssignParams()
    store.params.mz_precision_ppm = 8
    await nextTick()
    // Nothing is known to compare against yet, so nothing is recorded as an
    // override - a write here could not tell one from a value that merely
    // equals the default.
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('discards the pane old storage key rather than reading it', async () => {
    // It held {mzPrecision, formulaRange} - the pane's own names for two of
    // these fields, saved whether or not they differed from the defaults. Every
    // user has one; reading it would pin them all to the previous defaults.
    localStorage.setItem(
      LEGACY_STORAGE_KEY,
      JSON.stringify({ mzPrecision: 10, formulaRange: 'C0-100 H0-100 O0-100 N0-100' })
    )
    const store = await loadedStore()

    expect(store.params.mz_precision_ppm).toBe(SERVED.mz_precision_ppm)
    expect(store.params.formula_ranges).toBe(SERVED.formula_ranges)
    expect(localStorage.getItem(LEGACY_STORAGE_KEY)).toBeNull()
  })
})

describe('peak assignment parameters: the formula range', () => {
  it('accepts element ranges and isotopes in either notation', () => {
    expect(isFormulaRange('C0-80 H0-160 O0-50 N0-20')).toBe(true)
    expect(isFormulaRange('C0-80 [15N]0-1 ^N0-1 Cl0-10')).toBe(true)
    expect(isFormulaRange('  C0-80 H0-160  ')).toBe(true)
  })

  it('rejects a range that is still being typed', () => {
    expect(isFormulaRange('C0-80 H0-')).toBe(false)
    expect(isFormulaRange('C0-80 H')).toBe(false)
    expect(isFormulaRange('nonsense')).toBe(false)
    expect(isFormulaRange('')).toBe(false)
    expect(isFormulaRange(null)).toBe(false)
  })

  it('never stores a half-typed range', async () => {
    const store = await loadedStore()
    store.params.formula_ranges = 'C0-80 H0-'
    await nextTick()

    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('drops a stored range that no longer parses', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ formula_ranges: 'C0-' }))
    const store = await loadedStore()

    expect(store.params.formula_ranges).toBe(SERVED.formula_ranges)
  })
})

describe('peak assignment parameters: reset', () => {
  it('puts every field back and stops remembering', async () => {
    const store = await loadedStore()
    store.params.mz_precision_ppm = 8
    store.params.formula_ranges = 'C0-10'
    await nextTick()

    store.reset()
    await nextTick()

    expect({ ...store.params }).toEqual(SEARCH_DEFAULTS)
    expect(store.isDefault()).toBe(true)
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('clears only the fields it is given, and none it does not own', async () => {
    const store = await loadedStore()
    store.params.mz_precision_ppm = 8
    store.params.formula_ranges = 'C0-10'
    await nextTick()

    store.reset(['formula_ranges', 'max_alternatives'])
    await nextTick()

    expect(store.params.formula_ranges).toBe(SERVED.formula_ranges)
    expect(store.params).not.toHaveProperty('max_alternatives')
    expect(stored()).toEqual({ mz_precision_ppm: 8 })
  })

  it('reports per scope whether anything is overridden', async () => {
    const store = await loadedStore()
    store.params.mz_precision_ppm = 8

    expect(store.isDefault()).toBe(false)
    expect(store.isDefault(['mz_precision_ppm'])).toBe(false)
    expect(store.isDefault(['formula_ranges'])).toBe(true)
  })
})

// --- A batch's settings ----------------------------------------------------------

describe("a batch's settings as a form model and back", () => {
  const DEFAULTS = {
    run_untargeted: true,
    profile: 'auto',
    context: 'auto',
    mz_precision_ppm: null,
    formula_ranges: null,
    max_untargeted_peaks: null,
    peak_intensity_threshold: 0,
    max_alternatives: 5
  }

  it('fills what the batch did not save from the defaults', () => {
    expect(batchSettingsModel({ max_alternatives: 2 }, DEFAULTS)).toEqual({
      ...DEFAULTS,
      max_alternatives: 2
    })
    expect(batchSettingsModel(null, DEFAULTS)).toEqual(DEFAULTS)
    // Before /params answers, an unsaved field is empty.
    expect(batchSettingsModel({ profile: 'BR' }, null).max_alternatives).toBeNull()
  })

  it('offers no switch for the run: the batch pass is the search', () => {
    expect(BATCH_SETTING_KEYS).not.toContain('run_untargeted')
    expect(BATCH_SETTING_KEYS).toHaveLength(PARAM_KEYS.length - 1)
  })

  const body = (model, saved) =>
    batchSettingsBody({ ...batchSettingsModel(saved, DEFAULTS), ...model }, saved, DEFAULTS)

  it('writes a changed field and nothing else', () => {
    expect(body({ mz_precision_ppm: 4 }, null)).toEqual({
      settings: { mz_precision_ppm: 4 },
      changed: true
    })
  })

  it('carries what the batch saved and nobody touched, a default included', () => {
    // Saved through the SDK at today's default: left as it is.
    const saved = { max_alternatives: 5, run_untargeted: false }
    expect(body({ max_untargeted_peaks: 10 }, saved)).toEqual({
      settings: { max_alternatives: 5, run_untargeted: false, max_untargeted_peaks: 10 },
      changed: true
    })
  })

  it('leaves out a field put back on its default or cleared', () => {
    expect(body({ max_alternatives: 5 }, { max_alternatives: 2 })).toEqual({
      settings: null,
      changed: true
    })
    expect(body({ formula_ranges: '' }, { formula_ranges: 'C0-10' })).toEqual({
      settings: null,
      changed: true
    })
  })

  it('says nothing changed for an untouched dialog, in any key order', () => {
    expect(body({}, null)).toEqual({ settings: null, changed: false })
    expect(body({}, {})).toEqual({ settings: null, changed: false })
    expect(body({}, { profile: 'BR', max_alternatives: 2 })).toEqual({
      settings: { max_alternatives: 2, profile: 'BR' },
      changed: false
    })
  })

  it('never writes a range that does not parse', () => {
    expect(body({ formula_ranges: 'C0-' }, null)).toEqual({ settings: null, changed: false })
  })

  it('launches with every field that is set', () => {
    expect(runConfig({ ...DEFAULTS, formula_ranges: '', mz_precision_ppm: 3 })).toEqual({
      run_untargeted: true,
      profile: 'auto',
      context: 'auto',
      mz_precision_ppm: 3,
      peak_intensity_threshold: 0,
      max_alternatives: 5
    })
    expect(runConfig({ ...DEFAULTS, formula_ranges: 'C0-' }).formula_ranges).toBeUndefined()
    expect(runConfig(DEFAULTS, { run_untargeted: false }).run_untargeted).toBe(false)
  })

  it('launches with what the batch saved of the fields the form does not show', () => {
    // The tier bands and the rest of the engine's config a batch keeps: a
    // sample is assigned as its batch is in those too.
    const saved = { assigned_threshold: 0.9, stage_b_confidence: 0.5, max_alternatives: 2 }
    expect(runConfig({ ...DEFAULTS, max_alternatives: 7 }, {}, saved)).toEqual({
      assigned_threshold: 0.9,
      stage_b_confidence: 0.5,
      run_untargeted: true,
      profile: 'auto',
      context: 'auto',
      peak_intensity_threshold: 0,
      max_alternatives: 7
    })
  })

  it('keeps what the batch saved of a field the dialog does not show', () => {
    const saved = { assigned_threshold: 0.9 }
    expect(body({ max_alternatives: 2 }, saved)).toEqual({
      settings: { assigned_threshold: 0.9, max_alternatives: 2 },
      changed: true
    })
  })
})
