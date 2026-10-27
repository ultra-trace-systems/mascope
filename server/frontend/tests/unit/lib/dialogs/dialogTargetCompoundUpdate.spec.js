import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'

// The edit dialog opens on a match ion record, which carries the compound's
// name and formula but neither its CAS number nor its structure identity. It
// reads both from the stored compound list, and Save sends only the identity
// fields the user changed: a field sent empty would clear the stored value.
// The CAS number and the identity sit in a Structure group, collapsed unless
// one of them has a value. Its Find structure lists the reference rows of the
// compound's formula, and picking one fills the group.

const update = vi.fn()
const findStructures = vi.fn()
// Reactive, as the store's list is: a sync replaces its records.
const store = reactive({ list: [], update, findStructures })
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
const Tag = { props: ['value'], template: '<span class="tag">{{ value }}</span>' }

function open(compound = record) {
  return mount(DialogTargetCompoundUpdate, {
    props: { compound, visible: true },
    global: {
      stubs: {
        Dialog: { template: '<div><slot /></div>' },
        FloatLabel: { template: '<div><slot /></div>' },
        InputText,
        Panel,
        Button,
        Tag
      },
      directives: { tooltip: {} }
    }
  })
}

const button = (wrapper, label) =>
  wrapper.findAll('button').find((candidate) => candidate.text().startsWith(label))

const save = (wrapper) => button(wrapper, 'Save').trigger('click')

// Reference rows of methane's formula, as the structures route lists them:
// the row of the compound's CAS number first.
const ROWS = [
  {
    name: 'Methane',
    ...METHANE,
    cas_number: '74-82-8',
    source: 'comptox',
    source_native_id: 'DTXSID8025545',
    license: 'CC-BY-4.0',
    cas_match: true,
    name_match: true
  },
  {
    name: 'Marsh gas',
    inchikey: 'VNWKTOKETHGBQD-UHFFFAOYSA-N',
    inchi: null,
    smiles: null,
    cas_number: null,
    source: 'custom',
    source_native_id: 'row-7',
    license: 'CC0-1.0',
    cas_match: false,
    name_match: false
  }
]

const answer = (data, results = data.length) => ({ message: '', results, formula: 'CH4', data })

const find = async (wrapper) => {
  await button(wrapper, 'Find structure').trigger('click')
  await flushPromises()
}

const use = async (wrapper, index) => {
  await wrapper
    .findAll('button')
    .filter((candidate) => candidate.text() === 'Use')
    [index].trigger('click')
}

beforeEach(() => {
  update.mockClear()
  findStructures.mockReset()
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

  it('opens the Structure group for an identity alone', async () => {
    compounds.push({ ...record, inchikey: METHANE.inchikey })
    const wrapper = open()
    await nextTick()

    expect(wrapper.find('section').attributes('data-collapsed')).toBe('false')
  })

  it('finds the structures of the formula, the CAS number and the name', async () => {
    compounds.push(stored)
    findStructures.mockResolvedValue(answer(ROWS))
    const wrapper = open()
    await nextTick()

    await find(wrapper)

    expect(findStructures).toHaveBeenCalledWith({
      formula: 'CH4',
      cas_number: '74-82-8',
      name: 'Methane',
      page: 0,
      limit: 10
    })
    const listed = wrapper.findAll('li')
    expect(listed).toHaveLength(2)
    expect(listed[0].text()).toContain('Methane')
    expect(listed[0].text()).toContain(METHANE.inchikey)
    expect(listed[0].text()).toContain('CAS 74-82-8')
    expect(listed[0].text()).toContain('comptox · CC-BY-4.0')
    expect(listed[0].findAll('.tag').map((tag) => tag.text())).toEqual(['CAS', 'name'])
    expect(listed[1].text()).toContain('custom · CC0-1.0')
    expect(listed[1].text()).not.toContain('CAS')
  })

  it('fills a compound with a CAS number and no structure in two clicks', async () => {
    compounds.push(stored)
    findStructures.mockResolvedValue(answer(ROWS))
    const wrapper = open()
    await nextTick()

    await find(wrapper)
    await use(wrapper, 0)

    expect(wrapper.find('#edit-compound-inchikey').element.value).toBe(METHANE.inchikey)
    expect(wrapper.find('#edit-compound-inchi').element.value).toBe(METHANE.inchi)
    expect(wrapper.find('#edit-compound-smiles').element.value).toBe(METHANE.smiles)
    expect(wrapper.find('#add-compound-cas').element.value).toBe('74-82-8')
    expect(wrapper.find('[data-testid="picked"]').text()).toContain('comptox, CC-BY-4.0')
    // The list closes on the pick.
    expect(wrapper.findAll('li')).toHaveLength(0)

    await save(wrapper)
    expect(update).toHaveBeenCalledWith({
      target_compound_id: 'tc1',
      target_compound_formula: 'CH4',
      target_compound_name: 'Methane',
      cas_number: '74-82-8',
      ...METHANE
    })
  })

  it('replaces the whole structure with the picked row', async () => {
    compounds.push({ ...stored, ...METHANE, smiles: '[CH4]' })
    findStructures.mockResolvedValue(answer(ROWS))
    const wrapper = open()
    await nextTick()

    await find(wrapper)
    await use(wrapper, 1)
    await save(wrapper)

    // The row has a key alone: the InChI and the SMILES of the structure
    // stored before are cleared, not left beside another row's key.
    const [sent] = update.mock.calls[0]
    expect(sent).toMatchObject({ inchi: null, smiles: null })
    expect(sent).not.toHaveProperty('inchikey')
  })

  it("keeps the compound's CAS number when the row records none", async () => {
    compounds.push(stored)
    findStructures.mockResolvedValue(answer(ROWS))
    const wrapper = open()
    await nextTick()

    await find(wrapper)
    await use(wrapper, 1)

    expect(wrapper.find('#add-compound-cas').element.value).toBe('74-82-8')
  })

  it("takes the row's CAS number over the compound's", async () => {
    compounds.push({ ...stored, cas_number: '50-00-0' })
    findStructures.mockResolvedValue(answer(ROWS))
    const wrapper = open()
    await nextTick()

    await find(wrapper)
    await use(wrapper, 0)
    await save(wrapper)

    expect(update.mock.calls[0][0].cas_number).toBe('74-82-8')
  })

  it('lists the rows again on another lookup after a pick', async () => {
    compounds.push(stored)
    findStructures.mockResolvedValue(answer(ROWS))
    const wrapper = open()
    await nextTick()
    await find(wrapper)
    await use(wrapper, 0)

    await find(wrapper)

    expect(wrapper.find('[data-testid="picked"]').exists()).toBe(false)
    expect(wrapper.findAll('li')).toHaveLength(2)
  })

  it('asks once while a lookup is under way', async () => {
    compounds.push(stored)
    findStructures.mockReturnValue(new Promise(() => {}))
    const wrapper = open()
    await nextTick()

    await button(wrapper, 'Find structure').trigger('click')

    expect(button(wrapper, 'Find structure').attributes('disabled')).toBeDefined()
  })

  it('says so when no reference list has a structure for the formula', async () => {
    compounds.push(stored)
    findStructures.mockResolvedValue(answer([]))
    const wrapper = open()
    await nextTick()

    await find(wrapper)

    expect(wrapper.find('[data-testid="no-structures"]').text()).toBe(
      'No active reference list has a structure for CH4.'
    )
    expect(wrapper.findAll('li')).toHaveLength(0)
  })

  it('says why a lookup failed, and fills nothing', async () => {
    compounds.push(stored)
    findStructures.mockRejectedValue({ response: { data: { error: 'Lookup broke' } } })
    const wrapper = open()
    await nextTick()

    await find(wrapper)

    expect(wrapper.find('[role="alert"]').text()).toBe('Lookup broke')
    expect(wrapper.find('#edit-compound-inchikey').element.value).toBe('')
  })

  it('reads the next page with what the first page asked', async () => {
    compounds.push(stored)
    findStructures.mockResolvedValueOnce(answer([ROWS[0]], 2))
    const wrapper = open()
    await nextTick()
    await find(wrapper)

    // A CAS number typed since would reorder the rows under the next page.
    await wrapper.find('#add-compound-cas').setValue('50-00-0')
    findStructures.mockResolvedValueOnce(answer([ROWS[1]], 2))
    await button(wrapper, 'Show more').trigger('click')
    await flushPromises()

    expect(findStructures).toHaveBeenLastCalledWith({
      formula: 'CH4',
      cas_number: '74-82-8',
      name: 'Methane',
      page: 1,
      limit: 10
    })
    expect(wrapper.findAll('li').map((item) => item.text())).toHaveLength(2)
    expect(button(wrapper, 'Show more')).toBeUndefined()
  })

  it('drops an answer that arrives after it opened on another compound', async () => {
    compounds.push(stored)
    let resolve
    findStructures.mockReturnValue(new Promise((done) => (resolve = done)))
    const wrapper = open()
    await nextTick()
    await button(wrapper, 'Find structure').trigger('click')

    await wrapper.setProps({ compound: { ...record, target_compound_id: 'tc2' } })
    resolve(answer(ROWS))
    await flushPromises()

    expect(wrapper.findAll('li')).toHaveLength(0)
  })
})
