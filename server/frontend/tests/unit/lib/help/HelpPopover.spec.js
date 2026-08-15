import { describe, it, expect, vi, beforeEach } from 'vitest'
import { reactive, nextTick } from 'vue'
import { mount } from '@vue/test-utils'

// Reactive fake of the help store so the test can drive current/active.
const mocks = vi.hoisted(() => ({ help: null }))
vi.mock('@/stores', () => ({
  useApp: () => ({ ui: { help: mocks.help } })
}))

// Stub floating-ui so the component does not need real DOM measurement.
vi.mock('@floating-ui/vue', async () => {
  const { ref } = await import('vue')
  return {
    useFloating: () => ({ floatingStyles: ref({}), x: ref(0), y: ref(0), middlewareData: ref({}) }),
    arrow: () => ({}),
    offset: () => ({})
  }
})

import HelpPopover from '@/lib/help/HelpPopover.vue'

describe('HelpPopover hover persistence', () => {
  beforeEach(() => {
    mocks.help = reactive({
      current: null,
      active: false,
      resolveMessage: (card) => (card ? '<p>body</p>' : ''),
      toggle: () => {}
    })
  })

  const showCard = async () => {
    mocks.help.active = true
    mocks.help.current = { placement: 'bottom', doc: '/docs/how-it-works/matching/' }
    await nextTick()
  }

  it('hides when the card clears and the pointer is not on the popover', async () => {
    const wrapper = mount(HelpPopover)
    await showCard()
    expect(wrapper.find('.help-popover').exists()).toBe(true)

    mocks.help.current = null
    await nextTick()
    expect(wrapper.find('.help-popover').exists()).toBe(false)
  })

  it('stays open while the pointer is over it, so "Learn more" is clickable', async () => {
    const wrapper = mount(HelpPopover)
    await showCard()
    await wrapper.find('.help-popover').trigger('mouseenter')

    // The annotated element is no longer hovered, so the source card clears...
    mocks.help.current = null
    await nextTick()
    // ...but the popover stays because the pointer is on it.
    expect(wrapper.find('.help-popover').exists()).toBe(true)
    expect(wrapper.find('.help-learn-more').attributes('href')).toBe(
      '/docs/how-it-works/matching/'
    )

    await wrapper.find('.help-popover').trigger('mouseleave')
    await nextTick()
    expect(wrapper.find('.help-popover').exists()).toBe(false)
  })
})
