import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import PrimeVue from 'primevue/config'

import DatasetMetadataSection from '@/lib/dialogs/DatasetMetadataSection.vue'
import { metadataForm } from '@/lib/dialogs/datasetMetadata.js'

// The section edits through v-model: every change is one emitted form. The
// harness feeds each emitted form back in, as the dialog's v-model does.
function mountSection(form = metadataForm(null)) {
  const wrapper = mount(DatasetMetadataSection, {
    props: {
      modelValue: form,
      'onUpdate:modelValue': (value) => wrapper.setProps({ modelValue: value })
    },
    global: { plugins: [PrimeVue] }
  })
  return wrapper
}

const form = (wrapper) => wrapper.props('modelValue')
const button = (wrapper, label) =>
  wrapper.findAll('button').find((b) => b.attributes('aria-label') === label || b.text() === label)
const inputs = (wrapper, label) => wrapper.findAll(`input[aria-label="${label}"]`)

const AUTHORS = [
  { name: 'Ann', orcid: '', affiliation: '' },
  { name: 'Bob', orcid: '', affiliation: '' },
  { name: 'Cy', orcid: '', affiliation: '' }
]

describe('DatasetMetadataSection', () => {
  it('adds an author row and edits it in place', async () => {
    const wrapper = mountSection()

    await button(wrapper, 'Add author').trigger('click')
    expect(form(wrapper).authors).toEqual([{ name: '', orcid: '', affiliation: '' }])

    await inputs(wrapper, 'Author name')[0].setValue('Ann')
    await inputs(wrapper, 'Author ORCID')[0].setValue('0000-0002-1825-0097')
    expect(form(wrapper).authors).toEqual([
      { name: 'Ann', orcid: '0000-0002-1825-0097', affiliation: '' }
    ])
  })

  it('removes the author it is asked to and keeps the others in order', async () => {
    const wrapper = mountSection({ ...metadataForm(null), authors: AUTHORS })

    await wrapper.findAll('button[aria-label="Remove author"]')[1].trigger('click')
    expect(form(wrapper).authors.map((a) => a.name)).toEqual(['Ann', 'Cy'])
  })

  it('moves an author up', async () => {
    const wrapper = mountSection({ ...metadataForm(null), authors: AUTHORS })

    await wrapper.findAll('button[aria-label="Move author up"]')[2].trigger('click')
    expect(form(wrapper).authors.map((a) => a.name)).toEqual(['Ann', 'Cy', 'Bob'])
  })

  it('marks a malformed ORCID', async () => {
    const wrapper = mountSection({
      ...metadataForm(null),
      authors: [{ name: 'Ann', orcid: '1234', affiliation: '' }]
    })
    expect(inputs(wrapper, 'Author ORCID')[0].classes()).toContain('p-invalid')
  })

  it('turns a typed keyword into a chip on Enter, and removes it again', async () => {
    const wrapper = mountSection()
    const input = inputs(wrapper, 'Keyword')[0]

    await input.setValue(' aerosol ')
    await input.trigger('keydown', { key: 'Enter' })
    await input.setValue('CI-MS')
    await input.trigger('keydown', { key: 'Enter' })
    expect(form(wrapper).keywords).toEqual(['aerosol', 'CI-MS'])
    expect(input.element.value).toBe('')

    await wrapper.findAll('.p-chip-remove-icon')[0].trigger('click')
    expect(form(wrapper).keywords).toEqual(['CI-MS'])
  })

  it('adds and removes related-identifier and funding rows', async () => {
    const wrapper = mountSection()

    await button(wrapper, 'Add related identifier').trigger('click')
    await inputs(wrapper, 'Identifier')[0].setValue('10.1234/x')
    expect(form(wrapper).related_identifiers).toEqual([
      { identifier: '10.1234/x', relation: '', identifier_type: '' }
    ])

    await button(wrapper, 'Add funding').trigger('click')
    await inputs(wrapper, 'Funder')[0].setValue('Funder A')
    await inputs(wrapper, 'Award')[0].setValue('123')
    expect(form(wrapper).funding).toEqual([{ funder: 'Funder A', award: '123' }])

    await button(wrapper, 'Remove related identifier').trigger('click')
    await button(wrapper, 'Remove funding').trigger('click')
    expect(form(wrapper).related_identifiers).toEqual([])
    expect(form(wrapper).funding).toEqual([])
  })

  it('takes a licence that is not among the suggestions', async () => {
    const wrapper = mountSection()
    // The editable Select's own input carries the inputId
    await wrapper.find('#dataset-license').setValue('LicenseRef-custom')
    expect(form(wrapper).license).toBe('LicenseRef-custom')
  })
})
