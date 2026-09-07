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
  mz_precision_ppm: 3,
  formula_ranges: 'C0-80 H0-160 O0-50 N0-20',
  max_untargeted_peaks: 300,
  peak_intensity_threshold: 0,
  max_alternatives: 5
}

vi.mock('@/api', () => ({
  api: {
    http: {
      get: () =>
        Promise.resolve({
          data: {
            data: {
              params: {
                peak_assignment: SERVED,
                peak_assignment_limits: {
                  max_untargeted_peaks_ceiling: 2000,
                  max_mz_precision_ppm: 50,
                  max_alternatives_ceiling: 20
                },
                cheminfo_config: { DEBOUNCE_DELAY_MS: 800 }
              }
            }
          }
        })
    }
  }
}))

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
    props: ['modelValue', 'invalid'],
    emits: ['update:modelValue'],
    template:
      '<input class="text" :data-invalid="invalid" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" @blur="$emit(\'blur\')" />'
  },
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
