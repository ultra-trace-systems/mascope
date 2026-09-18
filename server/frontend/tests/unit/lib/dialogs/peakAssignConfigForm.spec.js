import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// The launcher form after its values moved into the shared parameter store: it
// no longer owns a config object the caller hands it, so what is worth pinning
// is the wiring the move created - the fields bind the shared record, and the
// reset control clears exactly the fields this form shows. A form that reset
// fields it does not render would silently discard the search pane's settings,
// or the other launcher's, from a button whose label promises nothing of the
// kind.

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

const PRESETS = {
  profiles: [
    { name: 'BR', label: 'Bromide CIMS', polarity: '-', default_context: 'ambient-air' },
    { name: 'UR', label: 'Uronium (urea) CIMS', polarity: '+', default_context: 'uronium' },
    { name: 'none', label: 'None', polarity: '', default_context: 'none' }
  ],
  contexts: [
    { name: 'ambient-air', label: 'Ambient air', description: 'Outdoor air.' },
    { name: 'uronium', label: 'Uronium', description: 'Urea CIMS matrix.' },
    { name: 'none', label: 'None', description: 'No matrix prior.' }
  ]
}

const PARAMS_RESPONSE = {
  data: {
    data: {
      params: {
        peak_assignment: SERVED,
        peak_assignment_limits: {
          max_untargeted_peaks_ceiling: 2000,
          max_mz_precision_ppm: 50,
          max_alternatives_ceiling: 20
        },
        peak_assignment_presets: PRESETS,
        cheminfo_config: { DEBOUNCE_DELAY_MS: 800 }
      }
    }
  }
}

// Every request the form makes other than /params is a profile preview. What
// it answers is the test's to set: a list of records, or a function of the
// request for an answer that depends on it, arrives late or fails.
let previewAnswer
const previewCalls = []
const get = vi.fn((url, config) => {
  if (url === '/params') return Promise.resolve(PARAMS_RESPONSE)
  previewCalls.push({ url, params: config?.params, errors: config?.errors })
  const answer = typeof previewAnswer === 'function' ? previewAnswer(url, config) : previewAnswer
  return Promise.resolve(answer).then((records) => ({ data: { data: records } }))
})
vi.mock('@/api', () => ({ api: { http: { get: (...args) => get(...args) } } }))

// The help layer the form registers its cards on; none of it is under test here.
const help = {
  directive: () => ({ mounted() {}, unmounted() {} }),
  docUrl: (path = '') => `/docs/${path}`,
  right: () => ({})
}
vi.mock('@/stores', () => ({ useApp: () => ({ ui: { help } }) }))

const PeakAssignConfigForm = (await import('@/lib/dialogs/PeakAssignConfigForm.vue')).default
const { usePeakAssignParams } = await import('@/lib/peakAssignParams')

// Plain controls so the form can be driven without installing the PrimeVue
// plugin, keeping the props the assertions read observable.
const stubs = {
  Button: {
    props: ['label', 'disabled'],
    template: '<button :disabled="disabled">{{ label }}</button>'
  },
  ToggleSwitch: {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template: '<input type="checkbox" class="toggle" :checked="modelValue" />'
  },
  InputNumber: {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template: '<input type="number" :value="modelValue" />'
  },
  InputText: {
    props: ['modelValue', 'invalid', 'placeholder'],
    emits: ['update:modelValue'],
    template:
      '<input class="text" :data-invalid="invalid" :placeholder="placeholder" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" @blur="$emit(\'blur\')" />'
  },
  Select: {
    props: ['modelValue', 'options', 'inputId'],
    emits: ['update:modelValue'],
    template:
      '<select :id="inputId" :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option></select>'
  },
  Message: { template: '<div class="message"><slot /></div>' },
  FloatLabel: { template: '<div><slot /></div>' }
}

async function mountForm(props = {}) {
  const wrapper = mount(PeakAssignConfigForm, { props, global: { stubs } })
  await flushPromises()
  return wrapper
}

const resetButton = (wrapper) =>
  wrapper.findAll('button').find((b) => b.text() === 'Reset to defaults')

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  get.mockClear()
  previewCalls.length = 0
  previewAnswer = []
})

describe('PeakAssignConfigForm', () => {
  it('opens on the shared values rather than filling a config the caller owns', async () => {
    const store = usePeakAssignParams()
    await mountForm()

    expect({ ...store.params }).toEqual(SERVED)
    expect(store.loaded).toBe(true)
  })

  it('carries a value set elsewhere into the fields', async () => {
    // Set in the search pane, or in the other launcher, or last session.
    const store = usePeakAssignParams()
    await store.ensureLoaded()
    store.params.mz_precision_ppm = 8

    const wrapper = await mountForm()
    const shown = wrapper.findAll('input[type="number"]').map((i) => i.element.value)
    expect(shown).toContain('8')
  })

  it('hides the switch the caller decides for itself', async () => {
    const shown = await mountForm()
    expect(shown.find('.toggle').exists()).toBe(true)

    const hidden = await mountForm({ hidden: ['run_untargeted'] })
    expect(hidden.find('.toggle').exists()).toBe(false)
  })
})

describe('PeakAssignConfigForm reset control', () => {
  it('is offered but inert while everything is still at the default', async () => {
    const wrapper = await mountForm()
    expect(resetButton(wrapper).attributes('disabled')).toBeDefined()
  })

  it('wakes up once a field is overridden, and puts it back', async () => {
    const store = usePeakAssignParams()
    const wrapper = await mountForm()

    store.params.max_alternatives = 9
    await wrapper.vm.$nextTick()
    expect(resetButton(wrapper).attributes('disabled')).toBeUndefined()

    await resetButton(wrapper).trigger('click')
    expect(store.params.max_alternatives).toBe(SERVED.max_alternatives)
    expect(store.isDefault()).toBe(true)
  })

  it('leaves a hidden field alone - it was never this form to set', async () => {
    // The batch launcher hides the untargeted switch because that button IS the
    // stage. Its reset must not reach across and flip the per-sample launcher's
    // switch back on, which is a setting made on a different screen.
    const store = usePeakAssignParams()
    await store.ensureLoaded()
    store.params.run_untargeted = false
    store.params.mz_precision_ppm = 8

    const wrapper = await mountForm({ hidden: ['run_untargeted'] })
    await resetButton(wrapper).trigger('click')

    expect(store.params.mz_precision_ppm).toBe(SERVED.mz_precision_ppm)
    expect(store.params.run_untargeted).toBe(false)
  })

  it('stays inert when only a hidden field is overridden', async () => {
    const store = usePeakAssignParams()
    await store.ensureLoaded()
    store.params.run_untargeted = false

    const wrapper = await mountForm({ hidden: ['run_untargeted'] })
    expect(resetButton(wrapper).attributes('disabled')).toBeDefined()
  })
})

describe('PeakAssignConfigForm formula range', () => {
  it('marks a half-typed range invalid and keeps it out of the shared record', async () => {
    const store = usePeakAssignParams()
    const wrapper = await mountForm()
    const field = wrapper.find('input.text')

    await field.setValue('C0-80 H0-')
    await field.trigger('blur')

    expect(wrapper.find('input.text').attributes('data-invalid')).toBe('true')
    expect(store.params.formula_ranges).toBe(SERVED.formula_ranges)
  })

  it('commits a range that parses', async () => {
    const store = usePeakAssignParams()
    const wrapper = await mountForm()
    const field = wrapper.find('input.text')

    await field.setValue('C0-40 H0-80 [15N]0-1')
    await field.trigger('blur')

    expect(store.params.formula_ranges).toBe('C0-40 H0-80 [15N]0-1')
  })
})

// --- The chemistry ---------------------------------------------------------------

/** A preview record as the server serves it. */
function resolution(overrides = {}) {
  return {
    profile: 'BR',
    profile_label: 'Bromide CIMS',
    profile_polarity: '-',
    requested_profile: 'auto',
    context: 'ambient-air',
    context_label: 'Ambient air',
    requested_context: 'auto',
    element_ranges: 'C1-40 H0-80 N0-3 O0-18 S0-1 Cl0-2 Br0-2',
    polarity: '-',
    samples: 1,
    ...overrides
  }
}

const optionTexts = (wrapper, id) =>
  wrapper.findAll(`select#${id} option`).map((option) => option.text())
const resolvedText = (wrapper) => wrapper.find('[data-testid="chemistry-resolved"]')
const polarityWarning = (wrapper) => wrapper.find('[data-testid="chemistry-polarity"]')

describe('PeakAssignConfigForm chemistry', () => {
  it('offers auto and every served preset, the identity entries by their own names', async () => {
    const wrapper = await mountForm()

    expect(optionTexts(wrapper, 'assign_profile')).toEqual([
      'Auto',
      'Bromide CIMS',
      'Uronium (urea) CIMS',
      'No profile'
    ])
    expect(optionTexts(wrapper, 'assign_context')).toEqual([
      'Auto',
      'Ambient air',
      'Uronium',
      'No context'
    ])
  })

  it('asks nothing when the launch names no sample or batch', async () => {
    const wrapper = await mountForm()
    expect(previewCalls).toEqual([])
    expect(resolvedText(wrapper).exists()).toBe(false)
  })

  it("names what auto resolves to on the sample, asked with the launch's own names", async () => {
    previewAnswer = [resolution()]
    const wrapper = await mountForm({ sampleItemId: 'si-1' })

    // Asked once: /params landing turns an unknown value into auto, which is
    // what the question already assumed.
    expect(previewCalls).toEqual([
      {
        url: '/peak-assignments/sample/si-1/profile-preview',
        params: { profile: 'auto', context: 'auto' },
        // A failed lookup is said beside the form, not toasted.
        errors: 'inline'
      }
    ])
    expect(optionTexts(wrapper, 'assign_profile')[0]).toBe('Auto (Bromide CIMS)')
    expect(optionTexts(wrapper, 'assign_context')[0]).toBe('Auto (Ambient air)')
    const text = resolvedText(wrapper).text()
    expect(text).toContain('Searches as')
    expect(text).toContain('Bromide CIMS · Ambient air')
    expect(text).toContain("Read off the sample's ionization mechanisms.")
  })

  it('binds a chosen profile to the shared record and asks again', async () => {
    const store = usePeakAssignParams()
    previewAnswer = (url, config) => [
      config.params.profile === 'UR'
        ? resolution({
            profile: 'UR',
            profile_label: 'Uronium (urea) CIMS',
            profile_polarity: '+',
            requested_profile: 'UR',
            context: 'uronium',
            context_label: 'Uronium',
            polarity: '+'
          })
        : resolution({ polarity: '+', profile: 'ESI_POS', profile_label: 'Positive ESI / APCI' })
    ]
    const wrapper = await mountForm({ sampleItemId: 'si-1' })

    await wrapper.find('select#assign_profile').setValue('UR')
    await flushPromises()

    expect(store.params.profile).toBe('UR')
    expect(previewCalls.at(-1).params).toEqual({ profile: 'UR', context: 'auto' })
    // Auto is no longer the choice, so its option stops naming an answer...
    expect(optionTexts(wrapper, 'assign_profile')[0]).toBe('Auto')
    // ...while the context, still auto, names the profile's own.
    expect(optionTexts(wrapper, 'assign_context')[0]).toBe('Auto (Uronium)')
    expect(resolvedText(wrapper).text()).toContain("The context is the profile's own.")
  })

  it('puts a named chemistry back on auto with the other fields', async () => {
    const store = usePeakAssignParams()
    const wrapper = await mountForm()
    store.params.profile = 'BR'
    store.params.context = 'none'
    await wrapper.vm.$nextTick()

    await resetButton(wrapper).trigger('click')
    expect(store.params.profile).toBe('auto')
    expect(store.params.context).toBe('auto')
  })

  it('shows the grid the run would search where the formula range is left empty', async () => {
    previewAnswer = [resolution()]
    const wrapper = await mountForm({ sampleItemId: 'si-1' })

    expect(wrapper.find('input.text').attributes('placeholder')).toBe(
      'C1-40 H0-80 N0-3 O0-18 S0-1 Cl0-2 Br0-2'
    )
  })

  it("lists each of a batch's answers with the samples it covers", async () => {
    previewAnswer = [
      resolution({ samples: 4 }),
      resolution({
        profile: 'ESI_NEG',
        profile_label: 'Negative ESI / APCI',
        context: 'none',
        context_label: 'None',
        samples: 1
      })
    ]
    const wrapper = await mountForm({ sampleBatchId: 'sb-1', hidden: ['run_untargeted'] })

    expect(previewCalls.at(-1).url).toBe('/peak-assignments/batch/sb-1/profile-preview')
    expect(optionTexts(wrapper, 'assign_profile')[0]).toBe('Auto (per sample)')
    const items = resolvedText(wrapper)
      .findAll('li')
      .map((item) => item.text())
    expect(items).toEqual([
      'Bromide CIMS · Ambient air · 4 samples',
      'Negative ESI / APCI · No context · 1 sample'
    ])
    expect(resolvedText(wrapper).text()).toContain("Read off each sample's ionization mechanisms.")
    // No one grid to show when the samples search different ones.
    expect(wrapper.find('input.text').attributes('placeholder')).toBe(
      "From each sample's chemistry profile"
    )
  })

  it('counts a batch that resolves alike as one answer', async () => {
    previewAnswer = [resolution({ samples: 6 })]
    const wrapper = await mountForm({ sampleBatchId: 'sb-1' })

    expect(resolvedText(wrapper).text()).toContain('All 6 samples search as')
    expect(optionTexts(wrapper, 'assign_profile')[0]).toBe('Auto (Bromide CIMS)')
  })

  it('warns when a named profile belongs to the other polarity from the sample', async () => {
    previewAnswer = [resolution({ requested_profile: 'BR', polarity: '+' })]
    const wrapper = await mountForm({ sampleItemId: 'si-1' })

    expect(polarityWarning(wrapper).text()).toBe(
      'Bromide CIMS is a negative-mode profile, and this sample is positive.'
    )
  })

  it("counts a batch's samples of the other polarity", async () => {
    previewAnswer = [
      resolution({ requested_profile: 'BR', polarity: '+', samples: 3 }),
      resolution({ requested_profile: 'BR', polarity: '-', samples: 2 })
    ]
    const wrapper = await mountForm({ sampleBatchId: 'sb-1' })

    expect(polarityWarning(wrapper).text()).toBe(
      'Bromide CIMS is a negative-mode profile, and 3 samples of this batch are positive.'
    )
  })

  it('does not warn for a profile of the same polarity, or for the identity profile', async () => {
    previewAnswer = [resolution()]
    const matching = await mountForm({ sampleItemId: 'si-1' })
    expect(polarityWarning(matching).exists()).toBe(false)

    previewAnswer = [
      resolution({
        profile: 'none',
        profile_label: 'None',
        profile_polarity: '',
        context: 'none',
        context_label: 'None',
        polarity: '+'
      })
    ]
    const identity = await mountForm({ sampleItemId: 'si-1' })
    expect(polarityWarning(identity).exists()).toBe(false)
    expect(resolvedText(identity).text()).toContain('No profile · No context')
  })

  it('keeps the later answer when an earlier one arrives last', async () => {
    const store = usePeakAssignParams()
    let releaseFirst
    previewAnswer = (url, config) =>
      config.params.profile === 'UR'
        ? [
            resolution({
              profile: 'UR',
              profile_label: 'Uronium (urea) CIMS',
              profile_polarity: '+'
            })
          ]
        : new Promise((resolve) => {
            releaseFirst = () => resolve([resolution()])
          })
    const wrapper = await mountForm({ sampleItemId: 'si-1' })

    store.params.profile = 'UR'
    await flushPromises()
    releaseFirst()
    await flushPromises()

    expect(resolvedText(wrapper).text()).toContain('Uronium (urea) CIMS')
    expect(resolvedText(wrapper).text()).not.toContain('Bromide CIMS')
  })

  it('says so when the chemistry could not be looked up', async () => {
    previewAnswer = () => Promise.reject(new Error('offline'))
    const wrapper = await mountForm({ sampleItemId: 'si-1' })

    expect(resolvedText(wrapper).text()).toBe(
      'Could not look the chemistry up. The run resolves it when it starts.'
    )
    expect(optionTexts(wrapper, 'assign_profile')[0]).toBe('Auto')
  })
})
