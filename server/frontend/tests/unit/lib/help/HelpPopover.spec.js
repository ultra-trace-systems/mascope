import { describe, it, expect, vi, beforeEach } from 'vitest'
import { reactive, nextTick } from 'vue'
import { mount } from '@vue/test-utils'

// Reactive fake of the help store so the test can drive current/active.
const mocks = vi.hoisted(() => ({ help: null }))
vi.mock('@/stores', () => ({
  useApp: () => ({ ui: { help: mocks.help } })
}))

// Stub floating-ui so the component does not need real DOM measurement. Each
// middleware factory returns a tagged object and records its options, so a test
// can assert what the component asked for without laying anything out.
const floating = vi.hoisted(() => ({ options: null, placement: null, middlewareData: null }))
vi.mock('@floating-ui/vue', async () => {
  const { ref } = await import('vue')
  const middleware = (name) => (options) => ({ name, options })
  return {
    useFloating: (target, popover, options) => {
      floating.options = options
      floating.placement = ref('bottom')
      floating.middlewareData = ref({})
      return {
        floatingStyles: ref({}),
        placement: floating.placement,
        middlewareData: floating.middlewareData
      }
    },
    arrow: middleware('arrow'),
    offset: middleware('offset'),
    flip: middleware('flip'),
    shift: middleware('shift'),
    size: middleware('size')
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

  it('bridges towards where the popover ended up, not where the card asked to be', async () => {
    const wrapper = mount(HelpPopover)
    await showCard()

    // No room below, so flip() put the card above its target instead. The gap is
    // now on the popover's underside and the bridge has to move with it, or the
    // pointer crosses untracked space on its way to "Learn more".
    floating.placement.value = 'top'
    await nextTick()
    const style = wrapper.find('.help-popover-bridge').attributes('style')
    expect(style).toContain('bottom: -12px')
    expect(style).not.toContain('top: -12px')
  })
})

describe('HelpPopover viewport fit', () => {
  let wrapper

  beforeEach(() => {
    mocks.help = reactive({
      current: null,
      active: false,
      resolveMessage: (card) => (card ? '<p>body</p>' : ''),
      toggle: () => {}
    })
    floating.options = null
    wrapper = mount(HelpPopover)
  })

  const middlewareNamed = (name) => floating.options.middleware.find((m) => m.name === name)

  it('asks floating-ui to keep the card on screen, in an order the middleware depends on', () => {
    // size() reads shift()'s data to tell whether an axis was free to move, and
    // arrow() has to run after both so its offset accounts for the result.
    expect(floating.options.middleware.map((m) => m.name)).toEqual([
      'offset',
      'flip',
      'shift',
      'size',
      'arrow'
    ])
  })

  it('leaves a gutter between the card and the edge it was pushed against', () => {
    for (const name of ['flip', 'shift', 'size']) {
      expect(middlewareNamed(name).options.padding).toBeGreaterThan(0)
    }
  })

  it('lets a card that fits on neither side of its target move beside it', () => {
    // Help is hung on whole panes as well as small controls, and a pane can be
    // taller than the room above and below it put together. Without a side-axis
    // fallback flip() would have nowhere to go and size() would squeeze the card
    // into whichever sliver was larger.
    expect(middlewareNamed('flip').options.fallbackAxisSideDirection).toBe('start')
  })

  it('caps the card at the room floating-ui reports, so a tall one scrolls instead of cropping', () => {
    const el = document.createElement('div')
    middlewareNamed('size').options.apply({ availableHeight: 240, elements: { floating: el } })
    expect(el.style.maxHeight).toBe('240px')
  })

  it('never asks for a negative height when the target is off screen', () => {
    const el = document.createElement('div')
    middlewareNamed('size').options.apply({ availableHeight: -40, elements: { floating: el } })
    expect(el.style.maxHeight).toBe('0px')
  })

  it("drops a short card's cap before measuring the next one", async () => {
    mocks.help.active = true
    mocks.help.current = { placement: 'bottom' }
    await nextTick()

    // What size() left behind after fitting a short card into a tight spot.
    const popover = wrapper.find('.help-popover').element
    popover.style.maxHeight = '120px'

    // A different card opens. Left in place, that cap is the height flip() would
    // measure, and a tall card would look as though it fits where it stands.
    mocks.help.current = { placement: 'bottom' }
    await nextTick()
    expect(popover.style.maxHeight).toBe('')
  })
})

describe('HelpPopover arrow', () => {
  let wrapper

  beforeEach(() => {
    mocks.help = reactive({
      current: null,
      active: false,
      resolveMessage: (card) => (card ? '<p>body</p>' : ''),
      toggle: () => {}
    })
    wrapper = mount(HelpPopover)
  })

  const showCardWithArrow = async (arrow) => {
    mocks.help.active = true
    mocks.help.current = { placement: 'bottom' }
    floating.middlewareData.value = { arrow }
    await nextTick()
    return wrapper.find('.help-popover-arrow').element.style
  }

  it('sits on the edge facing the target, drawn as a point towards it', async () => {
    // Card below its target: the arrow rides the popover's top edge and the two
    // borders on that side are the ones left drawn.
    const style = await showCardWithArrow({ x: 40 })
    expect(style.left).toBe('40px')
    expect(style.top).not.toBe('')
    expect(style.bottom).toBe('')
    expect(style.borderBottomStyle).toBe('none')
    expect(style.borderRightStyle).toBe('none')
  })

  it('moves to the other edge when the card is flipped, so it still points at the target', async () => {
    const style = await showCardWithArrow({ x: 40 })

    // flip() had to put the card above its target instead. The arrow belongs on
    // the opposite edge now, and the offsets it held on the old one have to go
    // with it or it would be pinned to both.
    floating.placement.value = 'top'
    await nextTick()
    expect(style.bottom).not.toBe('')
    expect(style.top).toBe('')
    expect(style.borderTopStyle).toBe('none')
    expect(style.borderLeftStyle).toBe('none')
  })
})
