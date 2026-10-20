import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import BaseEmptyState from '@/lib/base/BaseEmptyState.vue'

// What an empty list shows in place of nothing: what the list is for, how it
// fills, and the way forward when there is one.

describe('BaseEmptyState', () => {
  it('renders its title and hint', () => {
    const wrapper = mount(BaseEmptyState, {
      props: { title: 'No datasets yet', hint: 'A dataset groups batches.' }
    })

    expect(wrapper.get('.title').text()).toBe('No datasets yet')
    expect(wrapper.get('.hint').text()).toBe('A dataset groups batches.')
  })

  it('renders its icon', () => {
    const wrapper = mount(BaseEmptyState, {
      props: { title: 'Nothing yet', icon: 'pi ph ph-bell' }
    })

    expect(wrapper.get('.empty-icon').classes()).toEqual(
      expect.arrayContaining(['pi', 'ph', 'ph-bell'])
    )
  })

  it('renders the actions slot', () => {
    const wrapper = mount(BaseEmptyState, {
      props: { title: 'No batches yet' },
      slots: { actions: '<button>Create batch</button>' }
    })

    expect(wrapper.get('.actions button').text()).toBe('Create batch')
  })

  // A title alone (a search that matched nothing) leaves no empty hint line,
  // icon or action row behind.
  it('leaves out what it was not given', () => {
    const wrapper = mount(BaseEmptyState, { props: { title: 'No match' } })

    expect(wrapper.find('.hint').exists()).toBe(false)
    expect(wrapper.find('.empty-icon').exists()).toBe(false)
    expect(wrapper.find('.actions').exists()).toBe(false)
  })
})
