import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

// The shared untargeted-search parameters: one record behind the composition
// search pane, the per-sample assignment launcher and the batch untargeted
// search, which used to disagree about where a value comes from.
//
// What is worth pinning is not that a value round-trips but WHICH values are
// written: only genuine overrides, so a default that moves in a release moves
// with it for everyone who never touched the knob. Storing the whole record
// would silently freeze every user on the defaults of the release they first
// opened the dialog in - which is exactly what the pane's old storage key did,
// and why a shipped default change would not have reached them.

const SERVED = {
  run_untargeted: true,
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

const get = vi.fn()
vi.mock('@/api', () => ({ api: { http: { get: (...args) => get(...args) } } }))

// Imported after the mock (vi.mock is hoisted above it).
const { usePeakAssignParams, isFormulaRange, PARAM_KEYS } = await import('@/lib/peakAssignParams')

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
          cheminfo_config: { DEBOUNCE_DELAY_MS: 250 },
          ...rest
        }
      }
    }
  }
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
    expect({ ...store.params }).toEqual(SERVED)
    expect(store.loaded).toBe(true)
    expect(store.isDefault()).toBe(true)
  })

  it('takes the bounds and the search debounce from the same answer', async () => {
    const store = await loadedStore()
    expect(store.limits).toEqual(LIMITS)
    expect(store.debounceMs).toBe(250)
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
    store.params.max_alternatives = 9
    await nextTick()
    expect(stored()).toEqual({ max_alternatives: 9 })

    store.params.max_alternatives = SERVED.max_alternatives
    await nextTick()
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('keeps a threshold of zero apart from an unset one', async () => {
    // 0 is a meaningful value for the intensity threshold - it searches
    // everything the peak cap allows - so it must not be mistaken for "unset".
    get.mockResolvedValue(served({ peak_assignment: { ...SERVED, peak_intensity_threshold: 500 } }))
    const store = await loadedStore()
    store.params.peak_intensity_threshold = 0
    await nextTick()

    expect(stored()).toEqual({ peak_intensity_threshold: 0 })
  })

  it('restores an override on the next session and follows the default elsewhere', async () => {
    // The junk field stands for a name this store no longer owns: a record
    // written by another version must not smuggle one back into the params.
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ mz_precision_ppm: 8, retired_knob: 'nonsense' })
    )
    // A release moves the defaults under the user.
    get.mockResolvedValue(
      served({ peak_assignment: { ...SERVED, mz_precision_ppm: 4, max_alternatives: 12 } })
    )
    const store = await loadedStore()

    expect(store.params.mz_precision_ppm).toBe(8) // theirs, kept
    expect(store.params.max_alternatives).toBe(12) // the new default, followed
    expect(Object.keys(store.params).sort()).toEqual([...PARAM_KEYS].sort())
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

  it('neither stores nor launches with a half-typed range', async () => {
    // The launcher's field binds the store directly, so a range on its way to
    // being finished must not be persisted - and must not reach the pane's next
    // search, or the endpoint, as the committed value.
    const store = await loadedStore()
    store.params.formula_ranges = 'C0-80 H0-'
    await nextTick()

    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(store.payload().formula_ranges).toBeUndefined()
  })
})

describe('peak assignment parameters: reset', () => {
  it('puts every field back and stops remembering', async () => {
    const store = await loadedStore()
    store.params.mz_precision_ppm = 8
    store.params.max_alternatives = 9
    await nextTick()

    store.reset()
    await nextTick()

    expect({ ...store.params }).toEqual(SERVED)
    expect(store.isDefault()).toBe(true)
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('clears only the fields it is given', async () => {
    // The record is shared, so the search pane's reset must not discard the
    // peak ceiling a launcher dialog set - a field that pane never shows.
    const store = await loadedStore()
    store.params.mz_precision_ppm = 8
    store.params.max_untargeted_peaks = 42
    await nextTick()

    store.reset(['mz_precision_ppm', 'formula_ranges'])
    await nextTick()

    expect(store.params.mz_precision_ppm).toBe(SERVED.mz_precision_ppm)
    expect(store.params.max_untargeted_peaks).toBe(42)
    expect(stored()).toEqual({ max_untargeted_peaks: 42 })
  })

  it('reports per scope whether anything is overridden', async () => {
    const store = await loadedStore()
    store.params.max_untargeted_peaks = 42

    expect(store.isDefault()).toBe(false)
    expect(store.isDefault(['max_untargeted_peaks'])).toBe(false)
    // What a surface showing only these two would put on its reset control.
    expect(store.isDefault(['mz_precision_ppm', 'formula_ranges'])).toBe(true)
  })
})

describe('peak assignment parameters: the launch payload', () => {
  it('sends every field the user has, and lets the caller force its own', async () => {
    const store = await loadedStore()
    store.params.run_untargeted = false
    store.params.mz_precision_ppm = 8

    // The batch search IS the untargeted stage, so it forces the switch it does
    // not show, over whatever the shared record says.
    expect(store.payload({ run_untargeted: true })).toEqual({
      ...SERVED,
      mz_precision_ppm: 8,
      run_untargeted: true
    })
  })

  it('omits what is still unknown so the backend default applies', async () => {
    // A dialog opened before /params answered: a null would override the
    // server default rather than defer to it.
    const store = usePeakAssignParams()
    store.params.mz_precision_ppm = 8

    expect(store.payload()).toEqual({ mz_precision_ppm: 8 })
  })

  it('covers exactly the fields the config form offers', async () => {
    const store = await loadedStore()
    expect(Object.keys(store.payload()).sort()).toEqual([...PARAM_KEYS].sort())
  })
})
