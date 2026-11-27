import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { mount as mountComponent } from '@vue/test-utils'

import { EDGE, fitPlacement, usePopoverFit } from '@/lib/popoverFit.js'

// A popover stays inside the window: below its anchor where it fits, above
// where it fits there, and otherwise on the roomier side with its height
// capped, its content scrolling.

describe('fitPlacement', () => {
  const window700 = { viewportHeight: 700, gutter: 10 }

  it('opens below the anchor when it fits there', () => {
    const placed = fitPlacement({ ...window700, anchorTop: 100, anchorBottom: 120, height: 300 })
    expect(placed).toEqual({ side: 'below', top: 120, maxHeight: null })
  })

  it('opens above the anchor when there is no room below', () => {
    const placed = fitPlacement({ ...window700, anchorTop: 600, anchorBottom: 620, height: 300 })
    // Its bottom sits on the anchor; PrimeVue's flipped margin adds the gutter.
    expect(placed).toEqual({ side: 'above', top: 300, maxHeight: null })
  })

  it('counts the gutter and the edge gap in the room', () => {
    // Below: 700 - 120 - 10 - 8 = 562 px of room.
    const fits = { ...window700, anchorTop: 100, anchorBottom: 120 }
    expect(fitPlacement({ ...fits, height: 700 - 120 - 10 - EDGE }).side).toBe('below')
    expect(fitPlacement({ ...fits, height: 700 - 120 - 10 - EDGE + 1 }).maxHeight).not.toBeNull()
  })

  it('caps its height to the roomier side when it fits neither', () => {
    // The card's verdict bar near the window's foot: 572 px above, 52 below.
    const above = fitPlacement({ ...window700, anchorTop: 590, anchorBottom: 630, height: 800 })
    expect(above).toEqual({ side: 'above', top: 590 - 572, maxHeight: 572 })
    expect(above.top).toBe(10 + EDGE)

    // A row near the window's top: the room is below.
    const below = fitPlacement({ ...window700, anchorTop: 60, anchorBottom: 80, height: 800 })
    expect(below).toEqual({ side: 'below', top: 80, maxHeight: 700 - 80 - 10 - EDGE })
  })

  it('stays on the side it is on while it fits there', () => {
    // 182 px of room above, 462 below.
    const anchor = { ...window700, anchorTop: 200, anchorBottom: 220 }
    expect(fitPlacement({ ...anchor, height: 150 }).side).toBe('below')
    expect(fitPlacement({ ...anchor, height: 150, prefer: 'above' }).side).toBe('above')
    // Grown past the room above, it moves to where it fits.
    expect(fitPlacement({ ...anchor, height: 300, prefer: 'above' })).toEqual({
      side: 'below',
      top: 220,
      maxHeight: null
    })
  })

  it('never offers negative room for an anchor scrolled out of view', () => {
    const placed = fitPlacement({ ...window700, anchorTop: 690, anchorBottom: 760, height: 300 })
    expect(placed.side).toBe('above')
    expect(placed.maxHeight).toBeNull()
    // An anchor taller than the window leaves no room at all, never less.
    const tall = fitPlacement({ ...window700, anchorTop: -100, anchorBottom: 800, height: 300 })
    expect(tall.maxHeight).toBe(0)
  })
})

describe('usePopoverFit', () => {
  let observers
  let height

  beforeEach(() => {
    observers = []
    height = 300
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(callback) {
          this.callback = callback
          this.observed = []
          this.disconnect = vi.fn()
          observers.push(this)
        }
        observe(element) {
          this.observed.push(element)
        }
      }
    )
    vi.stubGlobal('innerHeight', 700)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  // A popover as PrimeVue renders it: a container and its content, appended to
  // the body, pointed at an anchor.
  function mount({ anchorTop, anchorBottom }) {
    const container = document.createElement('div')
    container.className = 'p-popover'
    const content = document.createElement('div')
    content.className = 'p-popover-content'
    const form = document.createElement('div')
    content.appendChild(form)
    container.appendChild(content)
    document.body.appendChild(container)
    // Its own height, unless a cap is on.
    Object.defineProperty(container, 'offsetHeight', {
      get: () => (container.style.maxHeight ? parseFloat(container.style.maxHeight) : height)
    })
    const anchor = document.createElement('button')
    anchor.getBoundingClientRect = () => ({ top: anchorTop, bottom: anchorBottom })
    const popover = ref({ container, target: anchor })
    return { popover, container, content, form, ...usePopoverFit(popover) }
  }

  it('flips a popover opened near the window foot above its anchor', () => {
    const { on, container, content } = mount({ anchorTop: 600, anchorBottom: 620 })
    on.show()
    expect(container.style.top).toBe('300px')
    expect(container.classList.contains('p-popover-flipped')).toBe(true)
    expect(container.getAttribute('data-p-popover-flipped')).toBe('true')
    expect(container.style.maxHeight).toBe('')
    expect(content.style.overflowY).toBe('')
  })

  it('leaves one that fits below unflipped', () => {
    const { on, container } = mount({ anchorTop: 100, anchorBottom: 120 })
    container.classList.add('p-popover-flipped')
    container.setAttribute('data-p-popover-flipped', 'true')
    on.show()
    expect(container.style.top).toBe('120px')
    expect(container.classList.contains('p-popover-flipped')).toBe(false)
    expect(container.hasAttribute('data-p-popover-flipped')).toBe(false)
  })

  it('caps one too tall for either side, and scrolls its content', () => {
    height = 900
    const { on, container, content } = mount({ anchorTop: 590, anchorBottom: 630 })
    on.show()
    expect(container.style.maxHeight).toBe('572px')
    expect(container.style.top).toBe(`${590 - 572}px`)
    expect(container.style.display).toBe('flex')
    expect(content.style.overflowY).toBe('auto')
    expect(content.style.minHeight).toBe('0')
  })

  it('places it again when its content grows, and lifts the cap when it shrinks', () => {
    const { on, container, content, form } = mount({ anchorTop: 600, anchorBottom: 620 })
    on.show()
    const [observer] = observers
    expect(observer.observed).toEqual([content, form])

    height = 900 // an evidence level picked: the form grows
    observer.callback()
    expect(container.style.maxHeight).toBe('582px')
    expect(container.style.top).toBe(`${600 - 582}px`)

    height = 250
    observer.callback()
    expect(container.style.maxHeight).toBe('')
    expect(content.style.overflowY).toBe('')
    expect(container.style.top).toBe('350px')
  })

  it('keeps the side it opened on while it fits there, and starts afresh when reopened', () => {
    // 482 px of room above, 162 below.
    const { on, container } = mount({ anchorTop: 500, anchorBottom: 520 })
    on.show()
    expect(container.style.top).toBe('200px')

    height = 100 // it would fit below now, but it stays where the user saw it
    observers[0].callback()
    expect(container.style.top).toBe('400px')
    expect(container.classList.contains('p-popover-flipped')).toBe(true)

    on.hide()
    on.show()
    expect(container.style.top).toBe('520px')
    expect(container.classList.contains('p-popover-flipped')).toBe(false)
  })

  it("reads the theme's gutter, and places it on the page as the window is scrolled", () => {
    vi.stubGlobal('scrollY', 40)
    height = 900
    const { on, container } = mount({ anchorTop: 590, anchorBottom: 630 })
    container.style.setProperty('--p-popover-gutter', '4px')
    on.show()
    // 590 - 4 - 8 = 578 px of room above.
    expect(container.style.maxHeight).toBe('578px')
    expect(container.style.top).toBe(`${590 - 578 + 40}px`)
  })

  it('stops watching when it hides', () => {
    const { on } = mount({ anchorTop: 600, anchorBottom: 620 })
    on.show()
    on.hide()
    expect(observers[0].disconnect).toHaveBeenCalled()
  })

  it('stops watching when the component that owns it unmounts', () => {
    const { container } = mount({ anchorTop: 600, anchorBottom: 620 })
    const owner = mountComponent(
      defineComponent({
        setup() {
          const fitting = usePopoverFit(ref({ container, target: document.body }))
          fitting.on.show()
          return () => h('div')
        }
      })
    )
    owner.unmount()
    expect(observers.at(-1).disconnect).toHaveBeenCalled()
  })

  it('does nothing before the popover has rendered', () => {
    const { fit } = usePopoverFit(ref(null))
    expect(() => fit()).not.toThrow()
    expect(() => usePopoverFit(ref({ container: null })).on.show()).not.toThrow()
  })
})
