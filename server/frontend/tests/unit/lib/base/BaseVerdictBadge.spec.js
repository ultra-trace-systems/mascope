import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@/stores', () => ({ useApp: () => ({ auth: { user: { id: 7 } } }) }))

const { default: BaseVerdictBadge } = await import('@/lib/base/BaseVerdictBadge.vue')

const RECORD = {
  verdict: 'confirmed',
  evidence_level: 'msms',
  verified_by: 7,
  verified_utc: '2026-09-04T10:00:00Z',
  assigned_formula: 'C6H12O6'
}

// The tooltip directive's value, kept on the element so the text can be read.
const tooltip = {
  mounted(el, binding) {
    el.dataset.tooltip = binding.value
  },
  updated(el, binding) {
    el.dataset.tooltip = binding.value
  }
}
const TagStub = {
  props: ['value', 'severity', 'icon'],
  template: '<span class="tag">{{ value }}</span>'
}
const mountBadge = (props) =>
  mount(BaseVerdictBadge, {
    props,
    global: { directives: { tooltip }, stubs: { Tag: TagStub } }
  })

describe('BaseVerdictBadge', () => {
  it('renders an owned compact verdict as its icon', () => {
    const wrapper = mountBadge({ record: RECORD, compact: true })
    const icon = wrapper.find('.verdict-icon')
    expect(icon.classes()).toContain('confirmed')
    expect(icon.classes()).not.toContain('inherited')
    expect(icon.attributes('data-tooltip')).toContain('Verified by you')
  })

  it('parenthesises a borrowed verdict and says where it came from', () => {
    const wrapper = mountBadge({ record: RECORD, compact: true, inherited: true })
    const icon = wrapper.find('.verdict-icon.inherited')
    expect(icon.exists()).toBe(true)
    expect(icon.text()).toBe('()')
    const tip = icon.attributes('data-tooltip')
    expect(tip).toContain('Confirmed · MS/MS · batch')
    expect(tip).toContain('Batch-level verdict on C6H12O6, by you')
    expect(tip).toContain('verifying this sample records an exception')
  })

  it('adds a line for a batch-level verdict that disagrees with the owned one', () => {
    const wrapper = mountBadge({ record: RECORD, compact: true, conflict: { verdict: 'rejected' } })
    const icon = wrapper.find('.verdict-icon')
    expect(icon.classes()).toContain('conflict')
    expect(icon.attributes('data-tooltip')).toContain('Batch-level verdict differs: Rejected')
  })

  it('marks a borrowed tag as inherited', () => {
    const wrapper = mountBadge({ record: RECORD, inherited: true })
    const tag = wrapper.find('.tag')
    expect(tag.classes()).toContain('inherited')
    expect(tag.text()).toBe('Confirmed · MS/MS · batch')
  })

  it('renders nothing without a record', () => {
    const wrapper = mountBadge({ record: null, compact: true })
    expect(wrapper.find('.verdict-icon').exists()).toBe(false)
    expect(wrapper.find('.tag').exists()).toBe(false)
  })
})
