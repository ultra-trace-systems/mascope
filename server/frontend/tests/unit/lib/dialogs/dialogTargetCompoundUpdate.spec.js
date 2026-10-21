import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'

// The edit dialog opens on a match ion record, which carries the compound's
// name and formula but neither its CAS number nor its structure identity. It
// reads both from the stored compound list, and Save sends only the identity
// fields the user changed: a field sent empty would clear the stored value.
// The CAS number and the identity sit in a Structure group, collapsed unless
// one of them has a value.

const update = vi.fn()
// Reactive, as the store's list is: a sync replaces its records.
const store = reactive({ list: [], update })
const compounds = store.list

vi.mock('@/stores', () => ({
  useApp: () => ({ data: { target: { compound: store } } })
}))

import DialogTargetCompoundUpdate from '@/lib/dialogs/DialogTargetCompoundUpdate.vue'

const METHANE = {
  inchikey: 'VNWKTOKETHGBQD-UHFFFAOYSA-N',
  inchi: 'InChI=1S/CH4/h1H4',
  smiles: 'C'
}

// A match ion row, as match_ions_controller returns it: name and formula, no
// CAS number and no identity.
const record = {
  target_compound_id: 'tc1',
  target_compound_formula: 'CH4',
  target_compound_name: 'Methane',
  target_ion_id: 'ti1'
}
// The compound as the list stores it.
const stored = { ...record, cas_number: '74-82-8' }

const InputText = {
  props: ['modelValue', 'id', 'invalid', 'disabled'],
  emits: ['update:modelValue'],
  template:
    '<input :id="id" :value="modelValue" :data-invalid="invalid" @input="$emit(\'update:modelValue\', $event.target.value)" />'
}
const Panel = {
  props: ['collapsed', 'header', 'toggleable'],
  template: '<section :data-collapsed="collapsed"><slot /></section>'
}
const Button = {
  props: ['label', 'disabled'],
  emits: ['click'],
  template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>'
}

function open() {
  return mount(DialogTargetCompoundUpdate, {
    props: { compound: record, visible: true },
    global: {
      stubs: {
        Dialog: { template: '<div><slot /></div>' },
        FloatLabel: { template: '<div><slot /></div>' },
        InputText,
        Panel,
        Button
      },
      directives: { tooltip: {} }
    }
  })
}

const save = (wrapper) =>
  wrapper
    .findAll('button')
    .find((button) => button.text() === 'Save')
    .trigger('click')

beforeEach(() => {
  update.mockClear()
  compounds.splice(0)
})

describe('DialogTargetCompoundUpdate', () => {
  it('shows the stored identity of the compound the record names', async () => {
    compounds.push({ ...stored, ...METHANE })
    const wrapper = open()
    await nextTick()

    expect(wrapper.find('#edit-compound-inchikey').element.value).toBe(METHANE.inchikey)
    expect(wrapper.find('#edit-compound-inchi').element.value).toBe(METHANE.inchi)
    expect(wrapper.find('#edit-compound-smiles').element.value).toBe(METHANE.smiles)
  })

  it('shows the stored CAS number, which the record lacks', async () => {
    compounds.push(stored)
    const wrapper = open()
    await nextTick()

    expect(wrapper.find('#add-compound-cas').element.value).toBe('74-82-8')
  })

  it('falls back on the record when the compound list does not hold it', async () => {
    const wrapper = mount(DialogTargetCompoundUpdate, {
      props: { compound: { ...record, ...METHANE }, visible: true },
      global: {
        stubs: {
          Dialog: { template: '<div><slot /></div>' },
          FloatLabel: { template: '<div><slot /></div>' },
          InputText,
          Panel,
          Button
        },
        directives: { tooltip: {} }
      }
    })
    await nextTick()

    expect(wrapper.find('#edit-compound-inchikey').element.value).toBe(METHANE.inchikey)
  })

  it('sends no identity field the user left alone', async () => {
    compounds.push({ ...stored, ...METHANE })
    const wrapper = open()
    await nextTick()

    await save(wrapper)

    expect(update).toHaveBeenCalledWith({
      target_compound_id: 'tc1',
      target_compound_formula: 'CH4',
      target_compound_name: 'Methane',
      cas_number: '74-82-8'
    })
  })

  it('clears nothing when the compound list does not hold the compound', async () => {
    const wrapper = open()
    await nextTick()

    await save(wrapper)

    const [sent] = update.mock.calls[0]
    expect(sent).not.toHaveProperty('inchikey')
    expect(sent).not.toHaveProperty('inchi')
    expect(sent).not.toHaveProperty('smiles')
  })

  it('sends an edited field, trimmed, and an emptied one as null', async () => {
    compounds.push({ ...stored, ...METHANE })
    const wrapper = open()
    await nextTick()

    await wrapper.find('#edit-compound-smiles').setValue(' [CH4] ')
    await wrapper.find('#edit-compound-inchi').setValue('')
    await save(wrapper)

    expect(update.mock.calls[0][0]).toMatchObject({ smiles: '[CH4]', inchi: null })
    expect(update.mock.calls[0][0]).not.toHaveProperty('inchikey')
  })

  it('will not save a malformed InChIKey', async () => {
    const wrapper = open()
    await nextTick()

    await wrapper.find('#edit-compound-inchikey').setValue('not-a-key')

    expect(wrapper.find('#edit-compound-inchikey').attributes('data-invalid')).toBe('true')
    const saveButton = wrapper.findAll('button').find((button) => button.text() === 'Save')
    expect(saveButton.attributes('disabled')).toBeDefined()
  })

  it('keeps what the user typed when the compound list syncs', async () => {
    compounds.push({ ...stored, ...METHANE })
    const wrapper = open()
    await nextTick()

    await wrapper.find('#add-compound-name').setValue('Marsh gas')
    await wrapper.find('#add-compound-cas').setValue('74-82-8 ')
    await wrapper.find('#edit-compound-smiles').setValue('[CH4]')
    // A socket reload: the same compound, as new records.
    compounds.splice(0, compounds.length, { ...stored, ...METHANE, smiles: 'C' })
    await nextTick()

    expect(wrapper.find('#add-compound-name').element.value).toBe('Marsh gas')
    expect(wrapper.find('#add-compound-cas').element.value).toBe('74-82-8 ')
    expect(wrapper.find('#edit-compound-smiles').element.value).toBe('[CH4]')
  })

  it('reads the stored identity once, not on every sync', async () => {
    compounds.push({ ...stored, ...METHANE })
    const wrapper = open()
    await nextTick()

    await wrapper.find('#add-compound-name').setValue('Marsh gas')
    compounds.splice(0, compounds.length, { ...stored, ...METHANE, smiles: '[CH4]' })
    await nextTick()

    expect(wrapper.find('#edit-compound-smiles').element.value).toBe(METHANE.smiles)
  })

  it('starts afresh when it is opened again', async () => {
    compounds.push({ ...stored, ...METHANE })
    const wrapper = open()
    await nextTick()
    await wrapper.find('#edit-compound-smiles').setValue('[CH4]')

    await wrapper.setProps({ visible: false })
    await wrapper.setProps({ visible: true })

    expect(wrapper.find('#edit-compound-smiles').element.value).toBe(METHANE.smiles)
  })

  it('takes the identity from a list that loads after it opened', async () => {
    const wrapper = open()
    await nextTick()

    compounds.push({ ...stored, ...METHANE })
    await nextTick()

    expect(wrapper.find('#edit-compound-inchikey').element.value).toBe(METHANE.inchikey)
    // Once: a later sync is no load.
    compounds.splice(0, compounds.length, { ...stored, ...METHANE, smiles: '[CH4]' })
    await nextTick()
    expect(wrapper.find('#edit-compound-smiles').element.value).toBe(METHANE.smiles)
    await save(wrapper)
    expect(update.mock.calls[0][0]).not.toHaveProperty('inchikey')
  })

  it('takes the CAS number from a list that loads after it opened', async () => {
    const wrapper = open()
    await nextTick()
    expect(wrapper.find('#add-compound-cas').element.value).toBe('')

    compounds.push(stored)
    await nextTick()

    expect(wrapper.find('#add-compound-cas').element.value).toBe('74-82-8')
  })

  it('keeps a CAS number typed before the list loaded', async () => {
    const wrapper = open()
    await nextTick()

    await wrapper.find('#add-compound-cas').setValue('50-00-0')
    compounds.push({ ...stored, ...METHANE })
    await nextTick()

    expect(wrapper.find('#add-compound-cas').element.value).toBe('50-00-0')
    expect(wrapper.find('#edit-compound-inchikey').element.value).toBe('')
  })

  it('keeps an identity typed before the list loaded', async () => {
    const wrapper = open()
    await nextTick()

    await wrapper.find('#edit-compound-smiles').setValue('[CH4]')
    compounds.push({ ...stored, ...METHANE })
    await nextTick()

    expect(wrapper.find('#edit-compound-smiles').element.value).toBe('[CH4]')
    expect(wrapper.find('#edit-compound-inchikey').element.value).toBe('')
  })

  it('reads the stored compound again when it opens on another', async () => {
    compounds.push({ ...record, ...METHANE }, { ...record, target_compound_id: 'tc2' })
    const wrapper = open()
    await nextTick()

    await wrapper.setProps({ compound: { ...record, target_compound_id: 'tc2' } })

    expect(wrapper.find('#edit-compound-inchikey').element.value).toBe('')
  })

  it('opens the Structure group only when it has something to show', async () => {
    compounds.push(record)
    const bare = open()
    await nextTick()
    expect(bare.find('section').attributes('data-collapsed')).toBe('true')

    // A stored CAS number alone opens it.
    compounds.splice(0, compounds.length, stored)
    const withCas = open()
    await nextTick()
    expect(withCas.find('section').attributes('data-collapsed')).toBe('false')
  })
})
