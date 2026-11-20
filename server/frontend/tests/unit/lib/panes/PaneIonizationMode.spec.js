import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'

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
  // A guest: the form is offered to anyone, the row actions to editors.
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

// Under Advanced the table lists the server's own modes, then the profiles
// Mascope ships. A profile's name, polarity and mechanisms are the chemistry's:
// an edit of one offers its collections alone, and it cannot be deleted.
describe('PaneIonizationMode list', () => {
  afterEach(() => {
    store.role = 100
    store.modes.splice(0, store.modes.length)
  })

  const row = (id, name, extra = {}) => ({
    ionization_mode_id: id,
    ionization_mode_name: name,
    ionization_mode_token: null,
    ionization_mode_polarity: '-',
    ionization_mechanism_ids: ['m1'],
    calibration_collection_id: null,
    diagnostic_collection_id: null,
    instrument: null,
    system_key: null,
    ...extra
  })

  const listed = () => {
    const tableRows = ref([])
    store.role = 200 // an editor, who is offered the row actions
    return mount(PaneIonizationMode, {
      global: {
        directives: { tooltip: {} },
        stubs: {
          ...stubs,
          Tag: { props: ['value'], template: '<span class="tag">{{ value }}</span>' },
          Button: {
            name: 'Button',
            props: ['label', 'disabled', 'icon'],
            emits: ['click'],
            template: '<button :data-icon="icon" @click="$emit(\'click\')" />'
          },
          DataTable: {
            props: ['value'],
            watch: { value: { handler: (value) => (tableRows.value = value), immediate: true } },
            template: '<div class="table"><slot /></div>'
          },
          Column: {
            props: ['header'],
            setup: () => ({ rows: tableRows }),
            template:
              '<div class="col" :data-header="header"><div v-for="row in rows" ' +
              ':key="row.ionization_mode_id" :data-row="row.ionization_mode_id">' +
              '<slot name="body" :data="row" /></div></div>'
          }
        }
      }
    })
  }

  const cells = (wrapper, id) => wrapper.findAll(`[data-row="${id}"]`)
  const inCell = (wrapper, header, id, selector) =>
    wrapper.find(`.col[data-header="${header}"] [data-row="${id}"]`).find(selector)
  const edit = async (wrapper, id) => {
    const pencil = cells(wrapper, id)
      .map((cell) => cell.find('button[data-icon="pi pi-pencil"]'))
      .find((button) => button.exists())
    await pencil.trigger('click')
  }

  const rows = () => [
    row('sysBromide', 'Bromide, negative', {
      system_key: 'bromide',
      calibration_collection_id: 'sysBromideCal'
    }),
    row('own', 'Bromide RI', { ionization_mode_token: 'Br' })
  ]

  it('lists the server own modes first, then the profiles', async () => {
    store.modes.splice(0, store.modes.length, ...rows())
    const wrapper = listed()
    await flushPromises()

    const order = wrapper
      .find('.col')
      .findAll('[data-row]')
      .map((cell) => cell.attributes('data-row'))
    expect(order).toEqual(['own', 'sysBromide'])
    expect(inCell(wrapper, 'Name', 'sysBromide', '.tag').text()).toBe('Shipped')
    expect(inCell(wrapper, 'Name', 'own', '.tag').exists()).toBe(false)
  })

  it('offers a profile no delete, and the server own mode one', async () => {
    store.modes.splice(0, store.modes.length, ...rows())
    const wrapper = listed()
    await flushPromises()

    const has = (id, selector) => cells(wrapper, id).some((cell) => cell.find(selector).exists())
    const deletable = 'button[data-icon="pi pi-trash"]'
    const locked = '[aria-label="Shipped with Mascope"]'
    expect(has('sysBromide', deletable)).toBe(false)
    expect(has('sysBromide', locked)).toBe(true)
    expect(has('own', deletable)).toBe(true)
    expect(has('own', locked)).toBe(false)
  })

  it('edits a profile collections alone, and every field of the server own', async () => {
    store.modes.splice(0, store.modes.length, ...rows())
    const wrapper = listed()
    await flushPromises()

    await edit(wrapper, 'sysBromide')
    for (const [header, field] of [
      ['Name', '.inputtext'],
      ['Filename token', '.inputtext'],
      ['Instrument', '.select'],
      ['Polarity', '.select'],
      ['Mechanisms', '.multiselect']
    ]) {
      expect(inCell(wrapper, header, 'sysBromide', field).exists(), header).toBe(false)
    }
    expect(inCell(wrapper, 'Calibration Collection', 'sysBromide', '.select').exists()).toBe(true)
    expect(inCell(wrapper, 'Diagnostic Collection', 'sysBromide', '.select').exists()).toBe(true)

    await edit(wrapper, 'own')
    expect(inCell(wrapper, 'Name', 'own', '.inputtext').exists()).toBe(true)
    expect(inCell(wrapper, 'Filename token', 'own', '.inputtext').exists()).toBe(true)
    expect(inCell(wrapper, 'Polarity', 'own', '.select').exists()).toBe(true)
    expect(inCell(wrapper, 'Mechanisms', 'own', '.multiselect').exists()).toBe(true)
    expect(inCell(wrapper, 'Instrument', 'own', '.select').exists()).toBe(true)
  })
})
