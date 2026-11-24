import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import BaseClaimEvidence from '@/lib/base/BaseClaimEvidence.vue'

const STUBS = {
  RadioButton: {
    props: ['modelValue', 'value', 'inputId'],
    emits: ['update:modelValue'],
    template:
      '<input type="radio" :id="inputId" :value="value" :checked="modelValue === value" @change="$emit(\'update:modelValue\', value)" />'
  }
}

function mountPicker(props) {
  return mount(BaseClaimEvidence, { props, global: { stubs: STUBS } })
}
const said = (wrapper) => wrapper.emitted('update:modelValue').at(-1)[0]
const choose = (wrapper, value) =>
  wrapper.findAll('input[type="radio"]').find((radio) => radio.attributes('value') === value)

describe('BaseClaimEvidence', () => {
  it('asks a standard for its independent line, none at first: 2b', () => {
    const wrapper = mountPicker({ evidence: 'reference_standard' })
    expect(wrapper.text()).toContain('An authentic standard on this instrument')
    expect(said(wrapper)).toEqual({
      evidence_detail: null,
      independent_line: null,
      ready: true,
      problem: null
    })
    expect(wrapper.text()).toContain('Level 2b. Would lift it: an independent line.')
  })

  it('takes a standard to level 1 with its line', async () => {
    const wrapper = mountPicker({ evidence: 'reference_standard' })
    await choose(wrapper, 'separation').trigger('change')
    expect(said(wrapper)).toMatchObject({ independent_line: 'separation', ready: true })
    expect(wrapper.text()).toContain('Level 1: a confirmed structure.')
  })

  it('waits for MS/MS to say what it showed', async () => {
    const wrapper = mountPicker({ evidence: 'msms' })
    expect(said(wrapper)).toEqual({
      evidence_detail: null,
      independent_line: null,
      ready: false,
      problem: 'Say what the MS/MS showed'
    })
    await choose(wrapper, 'loss').trigger('change')
    expect(said(wrapper)).toMatchObject({ evidence_detail: 'loss', ready: true })
    expect(wrapper.text()).toContain('Level 3b')
  })

  it('offers orthogonal evidence its letters', () => {
    const wrapper = mountPicker({ evidence: 'orthogonal' })
    const offered = wrapper.findAll('input[type="radio"]').map((r) => r.attributes('value'))
    expect(offered).toEqual(['separation', 'data_and_list', 'class', 'context_list', 'homologue'])
    expect(said(wrapper).ready).toBe(false)
  })

  it('starts a pattern at the split open, the server reading of none', async () => {
    const wrapper = mountPicker({ evidence: 'pattern' })
    expect(said(wrapper)).toMatchObject({ evidence_detail: 'split_open', ready: true })
    expect(wrapper.text()).toContain('Level 4b')
    await choose(wrapper, 'split_pinned').trigger('change')
    expect(said(wrapper).evidence_detail).toBe('split_pinned')
    expect(wrapper.text()).toContain('Level 4a')
  })

  it('asks nothing of a visual look', () => {
    const wrapper = mountPicker({ evidence: 'visual' })
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(0)
    expect(said(wrapper)).toEqual({
      evidence_detail: null,
      independent_line: null,
      ready: true,
      problem: null
    })
  })

  it('starts from what the verdict being changed said, on its own evidence', () => {
    const initial = {
      evidence_level: 'msms',
      context: { claim: { evidence_detail: 'library', independent_line: null } }
    }
    expect(said(mountPicker({ evidence: 'msms', initial })).evidence_detail).toBe('library')
    // On other evidence what it said does not carry over.
    expect(said(mountPicker({ evidence: 'orthogonal', initial })).evidence_detail).toBeNull()
    const standard = {
      evidence_level: 'reference_standard',
      context: { claim: { evidence_detail: null, independent_line: 'instrument' } }
    }
    expect(
      said(mountPicker({ evidence: 'reference_standard', initial: standard })).independent_line
    ).toBe('instrument')
  })

  it('starts again when the evidence level changes', async () => {
    const wrapper = mountPicker({ evidence: 'msms' })
    await choose(wrapper, 'fragments').trigger('change')
    await wrapper.setProps({ evidence: 'pattern' })
    expect(said(wrapper).evidence_detail).toBe('split_open')
  })
})
