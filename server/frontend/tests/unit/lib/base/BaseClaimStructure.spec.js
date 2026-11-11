import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import BaseClaimStructure from '@/lib/base/BaseClaimStructure.vue'

const GLUCOSE = 'WQZGKKKJIJFFOK-GASJEMHNSA-N'
const FRUCTOSE = 'RFSUNEUAIZKAJO-ARQDHWQXSA-N'
const CANDIDATES = [
  { inchikey: GLUCOSE, name: 'D-glucose', label: 'target' },
  { inchikey: FRUCTOSE, name: 'D-fructose', label: 'sugars' }
]

const STUBS = {
  RadioButton: {
    props: ['modelValue', 'value', 'inputId'],
    emits: ['update:modelValue'],
    template:
      '<input type="radio" :id="inputId" :value="value" :checked="modelValue === value" @change="$emit(\'update:modelValue\', value)" />'
  },
  InputText: {
    props: ['modelValue', 'invalid'],
    emits: ['update:modelValue'],
    template:
      '<input class="text" :data-invalid="String(!!invalid)" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />'
  }
}

function mountPicker(props) {
  return mount(BaseClaimStructure, {
    props: { candidates: CANDIDATES, ...props },
    global: { stubs: STUBS, directives: { tooltip: {} } }
  })
}
const claim = (wrapper) => wrapper.emitted('update:modelValue').at(-1)[0]
const options = (wrapper) => wrapper.findAll('label').map((label) => label.text())
const checked = (wrapper) => wrapper.find('input[type="radio"]:checked').attributes('value')
const choose = (wrapper, value) =>
  wrapper.findAll('input[type="radio"]').find((radio) => radio.attributes('value') === value)

describe('BaseClaimStructure', () => {
  it('offers the candidates in order, an unlisted structure last', () => {
    const wrapper = mountPicker({ evidence: 'msms' })
    expect(options(wrapper)).toEqual([
      'target · D-glucose',
      'sugars · D-fructose',
      'Unlisted: an InChIKey of my own'
    ])
  })

  it('picks the first candidate where the evidence names a structure', () => {
    const wrapper = mountPicker({ evidence: 'reference_standard' })
    expect(checked(wrapper)).toBe(GLUCOSE)
    expect(claim(wrapper)).toEqual({
      claimed_inchikey: GLUCOSE,
      claimed_name: 'D-glucose',
      ready: true,
      problem: null
    })
    expect(wrapper.text()).toContain('Level 1')
  })

  it('picks none on a pattern, where a structure is optional', () => {
    const wrapper = mountPicker({ evidence: 'pattern' })
    expect(options(wrapper)[0]).toBe('No structure')
    expect(checked(wrapper)).toBe('none')
    expect(claim(wrapper)).toEqual({
      claimed_inchikey: null,
      claimed_name: null,
      ready: true,
      problem: null
    })
  })

  it('fills the structure once the evidence comes to need one', async () => {
    const wrapper = mountPicker({ evidence: 'visual' })
    await wrapper.setProps({ evidence: 'orthogonal' })
    expect(claim(wrapper).claimed_inchikey).toBe(GLUCOSE)
    await wrapper.setProps({ evidence: 'pattern' })
    expect(claim(wrapper).claimed_inchikey).toBeNull()
  })

  it('keeps what the user picked when the evidence changes', async () => {
    const wrapper = mountPicker({ evidence: 'msms' })
    await choose(wrapper, FRUCTOSE).trigger('change')
    await wrapper.setProps({ evidence: 'pattern' })
    expect(claim(wrapper)).toMatchObject({ claimed_inchikey: FRUCTOSE, ready: true })
  })

  it('fills a structure the evidence comes to need, even after the user picked none', async () => {
    const wrapper = mountPicker({ evidence: 'pattern' })
    await choose(wrapper, 'none').trigger('change')
    await wrapper.setProps({ evidence: 'msms' })
    expect(claim(wrapper)).toMatchObject({ claimed_inchikey: GLUCOSE, ready: true })
  })

  it('names no structure when the one picked is no longer offered', async () => {
    const wrapper = mountPicker({ evidence: 'msms' })
    await choose(wrapper, FRUCTOSE).trigger('change')
    await wrapper.setProps({ candidates: [CANDIDATES[0]] })
    expect(claim(wrapper)).toEqual({
      claimed_inchikey: null,
      claimed_name: null,
      ready: false,
      problem: 'Name the structure this evidence identifies'
    })
  })

  it('takes an unlisted InChIKey and name, ready once the key is one', async () => {
    const wrapper = mountPicker({ evidence: 'msms' })
    await choose(wrapper, 'unlisted').trigger('change')
    const [key, name] = wrapper.findAll('input.text')
    expect(claim(wrapper)).toMatchObject({
      ready: false,
      problem: 'Name the structure this evidence identifies'
    })

    await key.setValue('not-a-key')
    expect(key.attributes('data-invalid')).toBe('true')
    expect(claim(wrapper)).toEqual({
      claimed_inchikey: null,
      claimed_name: null,
      ready: false,
      problem: expect.stringContaining('Not an InChIKey')
    })

    await key.setValue(` ${GLUCOSE} `)
    await name.setValue(' glucose ')
    expect(claim(wrapper)).toEqual({
      claimed_inchikey: GLUCOSE,
      claimed_name: 'glucose',
      ready: true,
      problem: null
    })
  })

  it('asks for an unlisted structure where the row has no candidate and one is needed', () => {
    const wrapper = mountPicker({ candidates: [], evidence: 'reference_standard' })
    expect(checked(wrapper)).toBe('unlisted')
    expect(claim(wrapper).ready).toBe(false)
  })

  it('names no structure for an empty unlisted key where none is needed', async () => {
    const wrapper = mountPicker({ evidence: 'pattern' })
    await choose(wrapper, 'unlisted').trigger('change')
    expect(claim(wrapper)).toEqual({
      claimed_inchikey: null,
      claimed_name: null,
      ready: true,
      problem: null
    })
    await wrapper.find('input.text').setValue('not-a-key')
    expect(claim(wrapper)).toMatchObject({
      ready: false,
      problem: expect.stringContaining('Not an InChIKey')
    })
  })

  it('starts a changed verdict from the candidate it names', () => {
    const wrapper = mountPicker({
      evidence: 'msms',
      initial: { claimed_inchikey: FRUCTOSE, claimed_name: 'D-fructose' }
    })
    expect(checked(wrapper)).toBe(FRUCTOSE)
    expect(claim(wrapper)).toMatchObject({ claimed_inchikey: FRUCTOSE, ready: true })
  })

  it('starts a changed verdict from its unlisted structure, key and name filled', () => {
    const OTHER = 'XLYOFNOQVPJJNP-UHFFFAOYSA-N'
    const wrapper = mountPicker({
      evidence: 'msms',
      initial: { claimed_inchikey: OTHER, claimed_name: 'water' }
    })
    expect(checked(wrapper)).toBe('unlisted')
    const [key, name] = wrapper.findAll('input.text')
    expect(key.element.value).toBe(OTHER)
    expect(name.element.value).toBe('water')
    expect(claim(wrapper)).toEqual({
      claimed_inchikey: OTHER,
      claimed_name: 'water',
      ready: true,
      problem: null
    })
  })

  it('takes up the structure of a verdict that arrives after the form opened', async () => {
    const wrapper = mountPicker({ evidence: 'msms' })
    expect(checked(wrapper)).toBe(GLUCOSE)
    await wrapper.setProps({ initial: { claimed_inchikey: FRUCTOSE, claimed_name: 'D-fructose' } })
    expect(checked(wrapper)).toBe(FRUCTOSE)
  })

  it('keeps the structure a changed pattern verdict names', async () => {
    const wrapper = mountPicker({
      evidence: 'pattern',
      initial: { claimed_inchikey: FRUCTOSE, claimed_name: 'D-fructose' }
    })
    expect(checked(wrapper)).toBe(FRUCTOSE)
    await wrapper.setProps({ evidence: 'visual' })
    expect(claim(wrapper)).toMatchObject({ claimed_inchikey: FRUCTOSE, ready: true })
  })

  it('keeps what the user types into an unlisted structure as the candidates change', async () => {
    const OTHER = 'XLYOFNOQVPJJNP-UHFFFAOYSA-N'
    const wrapper = mountPicker({
      evidence: 'msms',
      initial: { claimed_inchikey: OTHER, claimed_name: 'water' }
    })
    await wrapper.find('input.text').setValue(GLUCOSE.replace('GASJEMHNSA', 'UHFFFAOYSA'))
    await wrapper.setProps({ candidates: [CANDIDATES[1]] })
    expect(claim(wrapper).claimed_inchikey).toBe(GLUCOSE.replace('GASJEMHNSA', 'UHFFFAOYSA'))
  })

  it('keeps the name typed for an unlisted structure as the candidates change', async () => {
    const OTHER = 'XLYOFNOQVPJJNP-UHFFFAOYSA-N'
    const wrapper = mountPicker({
      evidence: 'msms',
      initial: { claimed_inchikey: OTHER, claimed_name: 'water' }
    })
    await wrapper.findAll('input.text')[1].setValue('ice')
    await wrapper.setProps({ candidates: [CANDIDATES[1]] })
    expect(claim(wrapper)).toMatchObject({ claimed_inchikey: OTHER, claimed_name: 'ice' })
  })
})
