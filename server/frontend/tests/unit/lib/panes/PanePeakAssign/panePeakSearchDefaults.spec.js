import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// Every pane goes when its test does, so no earlier pane can launch a search,
// or answer a notification, in a later test.
enableAutoUnmount(afterEach)

// What the composition search searches with while its two fields are empty. A
// run's defaults for both are "whatever the sample's chemistry profile resolves
// to", which /params serves as null; the pane asks the profile preview what
// that is for the focused sample - the grid and the instrument's window - and
// searches with it, as a run of the sample would.

const PEAK = { peak_id: 'p-1', mz: 250.1234, height: 1000 }
const RESOLVED = {
  profile: 'NO3',
  profile_label: 'Nitrate CIMS',
  context: 'ambient-air',
  context_label: 'Ambient air',
  element_ranges: 'C1-30 H0-60 N0-2 O0-20',
  mz_precision_ppm: 3,
  polarity: '-',
  samples: 1
}

// Each search is acknowledged as the route does it, with a 202 whose
// `Process-ID` header names the background task. The pane's notification
// handler is kept so a test can deliver what a search reports, and the result
// route serves one row for whichever search is asked for.
const { search, previewCalls, handlers, fetchResult } = vi.hoisted(() => {
  let launched = 0
  return {
    search: vi.fn(() =>
      Promise.resolve({ status: 202, headers: { 'process-id': `p-${++launched}` } })
    ),
    previewCalls: [],
    handlers: new Map(),
    fetchResult: vi.fn(() =>
      Promise.resolve([{ target_compound_formula: 'C10H16O4', ionization_mechanism_id: 'mech-1' }])
    )
  }
})

let focusedSampleId
// The focused sample itself, behind a getter so a test can focus one that has
// no ionization mode.
let focusedSample

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: {
      sample: {
        get focusedId() {
          return focusedSampleId.value
        },
        get focused() {
          return focusedSample.value
        }
      },
      peak: { list: [PEAK], focused: PEAK },
      target: { compound: { list: [] } },
      ionization: {
        mode: { list: [{ ionization_mode_id: 'mode-1', ionization_mechanism_ids: ['mech-1'] }] },
        mechanism: {
          list: [{ ionization_mechanism_id: 'mech-1', ionization_mechanism: '[M+NO3]-' }]
        }
      },
      match: { params: { typeDefaults: {} } },
      peakAssignment: { peak: { forPeak: () => null, curate: vi.fn() } }
    },
    ui: {
      help: {
        docUrl: (path = '') => `/docs/${path}`,
        top: () => ({}),
        bottom: () => ({}),
        bottom_end: () => ({}),
        left: () => ({}),
        right: () => ({})
      },
      notification: { on: (type, handler) => handlers.set(type, handler) }
    }
  })
}))

// /params with the run's defaults for both fields unset, and no debounce; the
// preview answers with RESOLVED for whichever sample it is asked about.
vi.mock('@/api', () => ({
  api: {
    http: {
      get: (url, config) => {
        if (url.startsWith('/cheminfo/mz/match/result/')) return fetchResult(url, config)
        if (url === '/params') {
          return Promise.resolve({
            data: {
              data: {
                params: {
                  peak_assignment: { mz_precision_ppm: null, formula_ranges: null },
                  cheminfo_config: { DEBOUNCE_DELAY_MS: 0 }
                }
              }
            }
          })
        }
        previewCalls.push({ url, params: config?.params })
        return Promise.resolve({ data: { data: [RESOLVED] } })
      },
      post: search
    }
  }
}))

vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: true }))
vi.mock('@/lib/base', () => ({
  BaseTierTag: { template: '<span />' },
  BaseMatchTag: { template: '<span />' }
}))
vi.mock('@/lib/dialogs', () => ({ PopoverTargetCompoundAdd: { template: '<span />' } }))
vi.mock('@/lib/panes/PanePeakAssign/preview.js', () => ({
  usePreview: () => ({ peak: ref(null) })
}))

const STUBS = {
  DataTable: true,
  Column: true,
  Button: true,
  FloatLabel: { template: '<div><slot /></div>' },
  MultiSelect: true,
  ProgressSpinner: true,
  InputNumber: {
    props: ['modelValue', 'inputId', 'placeholder'],
    template: '<input type="number" :id="inputId" :placeholder="placeholder" />'
  },
  InputText: {
    props: ['modelValue', 'placeholder'],
    emits: ['update:modelValue'],
    template:
      '<input class="text" :placeholder="placeholder" :value="modelValue" ' +
      '@input="$emit(\'update:modelValue\', $event.target.value)" />'
  }
}

const { default: PanePeakSearch } = await import('@/lib/panes/PanePeakAssign/PanePeakSearch.vue')
const { usePeakAssignParams } = await import('@/lib/peakAssignParams')

async function mountPane() {
  const wrapper = mount(PanePeakSearch, {
    props: { height: 400 },
    global: { stubs: STUBS, directives: { tooltip: {}, help: {} } }
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

// What the last search was asked with.
const lastSearch = () => search.mock.calls.at(-1)?.[1]
const rangeField = (wrapper) => wrapper.find('input#formulaRange')

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  focusedSampleId = ref('si-1')
  focusedSample = ref({ ionization_mode_id: 'mode-1' })
  previewCalls.length = 0
  handlers.clear()
})
afterEach(() => vi.clearAllMocks())

describe('PanePeakSearch with its fields left empty', () => {
  it("searches with the sample's chemistry profile window and grid", async () => {
    await mountPane()

    expect(previewCalls.at(-1)).toEqual({
      url: '/peak-assignments/sample/si-1/profile-preview',
      params: { profile: 'auto', context: 'auto' }
    })
    expect(search).toHaveBeenCalled()
    expect(lastSearch()).toMatchObject({
      mz_precision: 3,
      formula_ranges: 'C1-30 H0-60 N0-2 O0-20'
    })
  })

  it('shows what it searches with in the empty fields', async () => {
    const wrapper = await mountPane()

    expect(wrapper.find('input#mzPrecision').attributes('placeholder')).toBe('3')
    expect(rangeField(wrapper).attributes('placeholder')).toBe('C1-30 H0-60 N0-2 O0-20')
  })

  it('searches with a value typed in rather than the profile one', async () => {
    await mountPane()

    usePeakAssignParams().params.mz_precision_ppm = 5
    await flushPromises()

    expect(lastSearch()).toMatchObject({
      mz_precision: 5,
      formula_ranges: 'C1-30 H0-60 N0-2 O0-20'
    })
  })

  it("goes back to the profile's grid when the range is emptied", async () => {
    const wrapper = await mountPane()

    await rangeField(wrapper).setValue('C0-10 H0-20')
    await rangeField(wrapper).trigger('blur')
    await flushPromises()
    expect(lastSearch()).toMatchObject({ formula_ranges: 'C0-10 H0-20' })

    await rangeField(wrapper).setValue('')
    await rangeField(wrapper).trigger('blur')
    await flushPromises()
    expect(usePeakAssignParams().params.formula_ranges).toBeNull()
    expect(lastSearch()).toMatchObject({ formula_ranges: 'C1-30 H0-60 N0-2 O0-20' })
  })

  it('empties the range field when the range is put back on the profile', async () => {
    const wrapper = await mountPane()
    const store = usePeakAssignParams()
    store.params.formula_ranges = 'C0-10 H0-20'
    await flushPromises()
    expect(rangeField(wrapper).element.value).toBe('C0-10 H0-20')

    store.reset(['formula_ranges'])
    await flushPromises()

    expect(rangeField(wrapper).element.value).toBe('')
  })

  it('asks again for another sample', async () => {
    await mountPane()

    focusedSampleId.value = 'si-2'
    await flushPromises()

    expect(previewCalls.at(-1).url).toBe('/peak-assignments/sample/si-2/profile-preview')
  })
})

// Any line of a candidate's ion may be the peak - its 13C or 81Br line as well
// as its monoisotopic one - so the search asks for every line.
describe('PanePeakSearch isotopologue lines', () => {
  it("reads the peak as any line of each candidate's ion", async () => {
    await mountPane()

    expect(lastSearch()).toMatchObject({ isotopologues: true })
  })
})

// The completion notification of every search this user runs reaches the same
// socket room, so the pane tells its own search's apart by the process id the
// 202 acknowledging it named.
describe('PanePeakSearch waiting for its own search', () => {
  const acknowledged = async (call) => (await search.mock.results.at(call).value).headers

  it('waits for the process the latest search was acknowledged as', async () => {
    const wrapper = await mountPane()
    expect(wrapper.vm.pendingProcessId).toBe((await acknowledged(-1))['process-id'])

    usePeakAssignParams().params.mz_precision_ppm = 5
    await flushPromises()

    expect(lastSearch()).toMatchObject({ mz_precision: 5 })
    expect(wrapper.vm.pendingProcessId).toBe((await acknowledged(-1))['process-id'])
  })

  it('keeps waiting for the later search when an earlier one is acknowledged last', async () => {
    let acknowledgeFirst
    search.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          acknowledgeFirst = () => resolve({ status: 202, headers: { 'process-id': 'p-early' } })
        })
    )
    const wrapper = await mountPane()
    usePeakAssignParams().params.mz_precision_ppm = 5
    await flushPromises()
    const later = wrapper.vm.pendingProcessId

    acknowledgeFirst()
    await flushPromises()

    expect(later).toMatch(/^p-\d+$/)
    expect(wrapper.vm.pendingProcessId).toBe(later)
  })

  it('stops waiting when the search cannot be launched', async () => {
    search.mockImplementationOnce(() => Promise.reject(new Error('502 Bad Gateway')))
    const wrapper = await mountPane()

    expect(search).toHaveBeenCalled()
    expect(wrapper.vm.loading).toBe(false)
    expect(wrapper.vm.pendingProcessId).toBeNull()
  })
})

// A search this pane has moved on from still reports, and while the later
// search's own 202 is in flight there is no id of its own to tell the two
// apart by: the earlier one's report is the same peak's, and passes the peak
// check. So the pane remembers which searches it moved on from.
describe('PanePeakSearch ignoring the searches it moved on from', () => {
  /** A deferred 202 for `processId`, and the call that lets it land. */
  function held(processId) {
    let land
    search.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          land = () => resolve({ status: 202, headers: { 'process-id': processId } })
        })
    )
    return () => land()
  }

  /** What a search of the focused peak reports when it finishes. */
  async function finish(processId) {
    handlers.get('match_compositions_by_mz')({
      status: 'success',
      process_id: processId,
      data: { sample_item_id: 'si-1', mz: PEAK.mz, results: 1, total: 1 }
    })
    await flushPromises()
  }

  const fetched = () => fetchResult.mock.calls.map(([url]) => url)

  const searchAgain = async () => {
    usePeakAssignParams().params.mz_precision_ppm = 5
    await flushPromises()
  }

  it('ignores the earlier search that finishes while the later is acknowledged', async () => {
    const wrapper = await mountPane()
    const first = wrapper.vm.pendingProcessId
    const landSecond = held('p-second')
    await searchAgain()

    await finish(first)

    expect(fetched()).toEqual([])
    expect(wrapper.vm.loading).toBe(true)

    landSecond()
    await flushPromises()
    await finish('p-second')

    expect(fetched()).toEqual(['/cheminfo/mz/match/result/p-second'])
    expect(wrapper.vm.results).toHaveLength(1)
    expect(wrapper.vm.loading).toBe(false)
  })

  it('ignores an earlier search acknowledged only after the later started', async () => {
    const landFirst = held('p-first')
    const landSecond = held('p-second')
    const wrapper = await mountPane()
    await searchAgain()

    landFirst()
    await flushPromises()
    await finish('p-first')

    expect(wrapper.vm.pendingProcessId).toBeNull()
    expect(fetched()).toEqual([])

    landSecond()
    await flushPromises()
    await finish('p-second')

    expect(fetched()).toEqual(['/cheminfo/mz/match/result/p-second'])
  })

  // The launch that failed is the earlier one: the http layer's report of it
  // names no process, and the launch's own catch knows it is no longer the
  // latest, so the later search is still waited for.
  it('keeps waiting for the later search when the earlier launch fails', async () => {
    let failFirst
    search.mockImplementationOnce(() => new Promise((_, reject) => (failFirst = reject)))
    const wrapper = await mountPane()
    await searchAgain()

    failFirst(new Error('timeout of 20000ms exceeded'))
    handlers.get('match_compositions_by_mz')({
      type: 'match_compositions_by_mz',
      status: 'error',
      message: 'Request timed out. Please try again or contact support.'
    })
    await flushPromises()

    expect(wrapper.vm.loading).toBe(true)
    expect(wrapper.vm.pendingProcessId).toMatch(/^p-\d+$/)
  })
})

// A sample need not have an ionization mode the browser holds: an acquisition
// whose fragmentation scans were filed without a chemistry has none, and a mode
// can be deleted under a sample. The pane used to read the mode's mechanisms
// off `undefined` for such a sample, and the error took its watcher with it.
describe('PanePeakSearch on a sample without an ionization mode', () => {
  // Vue hands an error thrown in a watcher to the app's error handler rather
  // than to whoever mounted the pane, so mounting alone would not notice one.
  async function mountCatchingErrors() {
    const errors = []
    mount(PanePeakSearch, {
      props: { height: 400 },
      global: {
        stubs: STUBS,
        directives: { tooltip: {}, help: {} },
        config: { errorHandler: (error) => errors.push(error) }
      }
    })
    await flushPromises()
    await flushPromises()
    return errors
  }

  it.each([
    ['no mode at all', { ionization_mode_id: null }],
    ['a mode the browser does not hold', { ionization_mode_id: 'mode-gone' }]
  ])('reads the mechanisms of a sample with %s without an error', async (_, sample) => {
    focusedSample.value = sample

    expect(await mountCatchingErrors()).toEqual([])
  })

  it('launches no search, having no mechanism to search under', async () => {
    focusedSample.value = { ionization_mode_id: null }

    await mountPane()

    // The fields resolved as they do for any sample, so it is the missing
    // mechanisms that hold the search back, and nothing else.
    expect(previewCalls.length).toBeGreaterThan(0)
    expect(search).not.toHaveBeenCalled()
  })

  it('still searches a sample that has one', async () => {
    await mountPane()

    expect(search).toHaveBeenCalled()
    expect(lastSearch().ionization_mechanism_ids).toEqual(['mech-1'])
  })
})
