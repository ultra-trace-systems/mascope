import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

// The add popover is a quick path: it asks for a formula and a name. The CAS
// number and the structure identity are set afterwards in Edit compound. A
// compound that already exists is added as it is.

const update = vi.fn().mockResolvedValue(undefined)
const compounds = []

vi.mock('primevue/useconfirm', () => ({ useConfirm: () => ({ require: vi.fn() }) }))

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: {
      sample: { focusedId: null },
      match: { collection: { focused: null } },
      target: {
        compound: { list: compounds },
        collection: { detailed: null, update }
      }
    }
  })
}))

import PopoverTargetCompoundAdd from '@/lib/dialogs/PopoverTargetCompoundAdd.vue'

const collection = {
  target_collection_id: 'col1',
  target_collection_name: 'Targets',
  target_collection_type: 'TARGETS'
}

const InputText = {
  props: ['modelValue', 'id', 'disabled'],
  emits: ['update:modelValue'],
  template:
    '<input :id="id" :value="modelValue" :disabled="disabled" @input="$emit(\'update:modelValue\', $event.target.value)" />'
}
const Button = {
  props: ['label', 'disabled', 'icon'],
  emits: ['click'],
  template:
    '<button :data-icon="icon" :disabled="disabled" @click="$emit(\'click\', $event)">{{ label }}</button>'
}

function open() {
  return mount(PopoverTargetCompoundAdd, {
    props: { collection },
    global: {
      stubs: {
        Popover: { template: '<div><slot /></div>', methods: { hide() {}, toggle() {} } },
        FloatLabel: { template: '<div><slot /></div>' },
        InputText,
        Button
      },
      directives: { tooltip: {} }
    }
  })
}

const addButton = (wrapper) =>
  wrapper.findAll('button').find((button) => ['Add', 'Create'].includes(button.text()))

beforeEach(() => {
  update.mockClear()
  compounds.splice(0)
})

describe('PopoverTargetCompoundAdd', () => {
  it('asks for a formula and a name only', () => {
    const wrapper = open()

    expect(wrapper.findAll('input').map((input) => input.attributes('id'))).toEqual([
      'add-compound-formula',
      'add-compound-name'
    ])
  })

  it('creates a new compound from its formula and name alone', async () => {
    const wrapper = open()
    await wrapper.find('#add-compound-formula').setValue('CH4')
    await wrapper.find('#add-compound-name').setValue(' Methane ')

    await addButton(wrapper).trigger('click')
    await flushPromises()

    const [sent] = update.mock.calls[0]
    expect(sent.target_compounds_create).toEqual([
      { target_compound_formula: 'CH4', target_compound_name: 'Methane' }
    ])
  })

  it('adds an existing compound of the formula as it is', async () => {
    compounds.push({ target_compound_id: 'tc1', target_compound_formula: 'CH4' })
    const wrapper = open()
    await wrapper.find('#add-compound-formula').setValue('CH4')
    await nextTick()

    await addButton(wrapper).trigger('click')
    await flushPromises()

    const [sent] = update.mock.calls[0]
    expect(sent.target_compound_ids).toEqual(['tc1'])
    expect(sent.target_compounds_create).toBeUndefined()
  })
})
