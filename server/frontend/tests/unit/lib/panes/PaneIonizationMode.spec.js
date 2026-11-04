import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import PaneIonizationMode from '@/lib/panes/PaneIonizationMode.vue'

// A FloatLabel rests its label inside the field until something is chosen, and
// a Select draws its placeholder in that same spot - so a Select that has both
// reads as two texts on top of each other. MultiSelect is not affected: it
// lifts its label, which is why the mechanisms field keeps its placeholder.
//
// happy-dom computes none of that positioning, so this pins the prop that
// causes it rather than the overlap itself.

const store = vi.hoisted(() => ({
  modes: [],
  create: null
}))

vi.mock('@/stores', async () => {
  const { reactive } = await import('vue')
  const { vi } = await import('vitest')
  store.modes = reactive([])
  store.create = vi.fn()
  return {
    useApp: () => ({
      auth: { user: { role_id: 100 } },
      data: {
        instrument: { list: [] },
        target: { collection: { list: [] } },
        ionization: {
          mechanism: { list: [] },
          mode: { list: store.modes, create: store.create, update: vi.fn(), delete: vi.fn() }
        }
      }
    })
  }
})
vi.mock('primevue/useconfirm', () => ({ useConfirm: () => ({ require: vi.fn() }) }))

const recording = (name) => ({
  props: ['modelValue', 'options', 'placeholder', 'id', 'showClear', 'disabled'],
  template: `<div class="${name}" :data-id="id" :data-placeholder="placeholder"></div>`
})

const stubs = {
  FloatLabel: { template: '<div class="float-label"><slot /></div>' },
  Select: recording('select'),
  MultiSelect: recording('multiselect'),
  InputText: recording('inputtext'),
  Button: {
    name: 'Button',
    props: ['label', 'disabled'],
    emits: ['click'],
    template: '<button />'
  },
  DataTable: true,
  Column: true,
  IconField: true,
  InputIcon: true,
  Fieldset: { template: '<div><slot /></div>' }
}

const pane = () => mount(PaneIonizationMode, { global: { stubs, directives: { tooltip: {} } } })

describe('PaneIonizationMode add form', () => {
  it('gives no Select inside a FloatLabel a placeholder to collide with', () => {
    const wrapped = pane().findAll('.float-label .select')
    expect(wrapped.length).toBeGreaterThan(0)
    expect(
      wrapped.map((select) => [select.attributes('data-id'), select.attributes('data-placeholder')])
    ).toEqual(wrapped.map((select) => [select.attributes('data-id'), undefined]))
  })

  it('still offers the instrument field, clearable back to every instrument', () => {
    const instrument = pane().find('.float-label .select[data-id="add-instrument"]')
    expect(instrument.exists()).toBe(true)
  })
})

describe('PaneIonizationMode create form', () => {
  const nameField = (wrapper) =>
    wrapper
      .findAllComponents(stubs.InputText)
      .find((input) => input.props('id') === 'add-mode-name')
  const typed = async (wrapper) => {
    nameField(wrapper).vm.$emit('update:modelValue', 'Half typed')
    await flushPromises()
  }

  it('is kept when a row is replaced by its next version', async () => {
    const wrapper = pane()
    store.modes.push({ ionization_mode_id: 'v1', ionization_mode_name: 'Nitrate' })
    await flushPromises()
    await typed(wrapper)

    // An edit that makes a mode's next version: one row leaves, one comes.
    store.modes.splice(0, store.modes.length)
    await flushPromises()
    store.modes.push({ ionization_mode_id: 'v2', ionization_mode_name: 'Nitrate' })
    await flushPromises()

    expect(nameField(wrapper).props('modelValue')).toBe('Half typed')
    store.modes.splice(0, store.modes.length)
  })

  it('is cleared once a create succeeds, and only then', async () => {
    store.create.mockResolvedValueOnce(undefined)
    store.create.mockResolvedValueOnce({ ionization_mode_id: 'new' })
    const wrapper = pane()
    await typed(wrapper)
    const create = wrapper
      .findAllComponents({ name: 'Button' })
      .find((button) => button.props('label') === 'Create')

    await create.vm.$emit('click')
    await flushPromises()
    expect(nameField(wrapper).props('modelValue')).toBe('Half typed')

    await create.vm.$emit('click')
    await flushPromises()
    expect(nameField(wrapper).props('modelValue')).toBe('')
  })
})
