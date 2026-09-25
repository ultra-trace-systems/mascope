import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

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

const { search, previewCalls } = vi.hoisted(() => ({
  search: vi.fn(() => Promise.resolve({})),
  previewCalls: []
}))

let focusedSampleId

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: {
      sample: {
        get focusedId() {
          return focusedSampleId.value
        },
        focused: { ionization_mode_id: 'mode-1' }
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
      notification: { on: vi.fn() }
    }
  })
}))

// /params with the run's defaults for both fields unset, and no debounce; the
// preview answers with RESOLVED for whichever sample it is asked about.
vi.mock('@/api', () => ({
  api: {
    http: {
      get: (url, config) => {
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
  previewCalls.length = 0
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
