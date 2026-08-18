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

  const showCard = async (doc = '/docs/how-it-works/matching/') => {
    mocks.help.active = true
    mocks.help.current = { placement: 'bottom', doc }
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
    expect(wrapper.find('.help-learn-more').attributes('href')).toBe('/docs/how-it-works/matching/')

    await wrapper.find('.help-popover').trigger('mouseleave')
    await nextTick()
    expect(wrapper.find('.help-popover').exists()).toBe(false)
  })

  it('keeps its own card while the pointer is on it, even if another card takes over', async () => {
    // The popover covers other annotated elements, and hover detection is
    // geometric, so the covered element becomes the store's current card while
    // the pointer is still travelling to "Learn more".
    const wrapper = mount(HelpPopover)
    await showCard()
    await wrapper.find('.help-popover').trigger('mouseenter')

    mocks.help.current = { placement: 'right', doc: '/docs/guides/covered-element/' }
    await nextTick()

    expect(wrapper.find('.help-learn-more').attributes('href')).toBe('/docs/how-it-works/matching/')

    // Once the pointer leaves, the covered element's card takes over as usual.
    await wrapper.find('.help-popover').trigger('mouseleave')
    await nextTick()
    expect(wrapper.find('.help-learn-more').attributes('href')).toBe(
      '/docs/guides/covered-element/'
    )
  })

  it('closes when help mode is switched off while the pointer is on it', async () => {
    const wrapper = mount(HelpPopover)
    await showCard()
    await wrapper.find('.help-popover').trigger('mouseenter')

    mocks.help.active = false
    await nextTick()
    expect(wrapper.find('.help-popover').exists()).toBe(false)

    // The pin is cleared on close, so the next card still shows.
    await showCard('/docs/guides/next-card/')
    expect(wrapper.find('.help-learn-more').attributes('href')).toBe('/docs/guides/next-card/')
  })

  it('bridges the offset gap towards the target so the pointer never leaves it', async () => {
    const wrapper = mount(HelpPopover)
    await showCard()
    // Card placed below its target: the bridge reaches back up over the gap.
    expect(wrapper.find('.help-popover-bridge').attributes('style')).toContain('top: -12px')
  })
})
