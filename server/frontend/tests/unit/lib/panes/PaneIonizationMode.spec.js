import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { h, inject, provide } from 'vue'

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
  create: null,
  confirm: null,
  role: 100
}))

vi.mock('@/stores', async () => {
  const { reactive } = await import('vue')
  const { vi } = await import('vitest')
  store.modes = reactive([])
  store.create = vi.fn()
  return {
    useApp: () => ({
      auth: { user: { role_id: store.role } },
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
vi.mock('primevue/useconfirm', async () => {
  const { vi } = await import('vitest')
  store.confirm = vi.fn()
  return { useConfirm: () => ({ require: store.confirm }) }
})

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

describe('PaneIonizationMode edit confirmation', () => {
  // The table's rows render here, so a row's edit and save buttons can be
  // pressed: each row provides itself, and each column renders its body for
  // that row.
  const RowScope = {
    props: ['row'],
    setup(props, { slots }) {
      provide('row', props.row)
      return () => slots.default?.()
    }
  }
  const tableStubs = {
    ...stubs,
    DataTable: {
      props: ['value'],
      setup(props, { slots }) {
        return () =>
          h(
            'div',
            props.value.map((row) => h(RowScope, { row }, () => slots.default?.()))
          )
      }
    },
    Column: {
      inheritAttrs: false,
      setup(_, { slots }) {
        const row = inject('row')
        return () => slots.body?.({ data: row })
      }
    },
    Button: {
      name: 'Button',
      props: ['label', 'disabled', 'icon'],
      emits: ['click'],
      template: '<button />'
    }
  }
  const mode = {
    ionization_mode_id: 'v1',
    ionization_mode_name: 'Nitrate',
    ionization_mode_token: 'NO3',
    ionization_mode_polarity: '-',
    ionization_mechanism_ids: ['m1'],
    calibration_collection_id: 'cal',
    diagnostic_collection_id: 'diag',
    version: 1
  }
  const button = (wrapper, icon) =>
    wrapper.findAllComponents({ name: 'Button' }).find((b) => b.props('icon') === icon)
  const field = (wrapper, stub, id) =>
    wrapper.findAllComponents(stub).find((input) => input.attributes('data-id') === id)

  const confirmed = async (edit) => {
    store.confirm.mockClear()
    store.role = 300
    store.modes.push({ ...mode })
    const wrapper = mount(PaneIonizationMode, {
      global: { stubs: tableStubs, directives: { tooltip: {} } }
    })
    await flushPromises()
    await button(wrapper, 'pi pi-pencil').vm.$emit('click')
    await flushPromises()
    await edit(wrapper)
    await flushPromises()
    await button(wrapper, 'pi pi-check').vm.$emit('click')
    await flushPromises()
    store.modes.splice(0, store.modes.length)
    store.role = 100
    expect(store.confirm).toHaveBeenCalledTimes(1)
    return store.confirm.mock.calls[0][0].message
  }

  it('tells a diagnostic-only edit that analysis batches keep their status', async () => {
    const message = await confirmed((wrapper) =>
      field(wrapper, tableStubs.Select, 'edit-diagnostic').vm.$emit('update:modelValue', 'other')
    )
    expect(message).toContain('Changing the diagnostic collection makes version 2')
    expect(message).toContain('Daily acquisition batches')
    expect(message).toContain('Analysis batches keep their status')
    expect(message).not.toContain('mechanisms')
  })

  it('tells a mechanisms edit that every batch is flagged for re-matching', async () => {
    const message = await confirmed((wrapper) =>
      wrapper
        .findAllComponents(tableStubs.MultiSelect)
        .find((input) => input.attributes('data-placeholder') === 'Select mechanisms')
        .vm.$emit('update:modelValue', ['m1', 'm2'])
    )
    expect(message).toContain('Changing the ionization mechanisms makes version 2')
    expect(message).toContain('Batches containing them will be flagged for re-matching')
    expect(message).not.toContain('Analysis batches')
  })

  it('tells a mechanisms and diagnostic edit that every batch is flagged', async () => {
    const message = await confirmed(async (wrapper) => {
      wrapper
        .findAllComponents(tableStubs.MultiSelect)
        .find((input) => input.attributes('data-placeholder') === 'Select mechanisms')
        .vm.$emit('update:modelValue', ['m1', 'm2'])
      field(wrapper, tableStubs.Select, 'edit-diagnostic').vm.$emit('update:modelValue', 'other')
    })
    expect(message).toContain('Changing the ionization mechanisms')
    expect(message).not.toContain('Analysis batches')
  })
})
