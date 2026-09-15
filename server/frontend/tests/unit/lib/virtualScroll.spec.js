import { describe, it, expect, vi } from 'vitest'

import { scrollVirtualRowIntoView } from '@/lib/virtualScroll'

/**
 * A DataTable stand-in shaped like PrimeVue's: the scroller's container under
 * `$el`, and the component's `getVirtualScrollerRef()` handing out the
 * VirtualScroller instance. `scroller: null` models a table that has one but
 * offers no `scrollInView`; `accessor: false`, a table with no accessor at all.
 */
function table({ scrollInView = null, accessor = true } = {}) {
  const container = { scrollTop: 0 }
  const scroller = scrollInView ? { scrollInView } : {}
  return {
    container,
    $el: { querySelector: (selector) => (selector === '.p-virtualscroller' ? container : null) },
    ...(accessor ? { getVirtualScrollerRef: () => scroller } : {})
  }
}

describe('scrollVirtualRowIntoView', () => {
  it("uses the scroller's own minimal scroll when it offers one", () => {
    const scrollInView = vi.fn()
    const t = table({ scrollInView })
    expect(scrollVirtualRowIntoView(t, 12)).toBe(true)
    expect(scrollInView).toHaveBeenCalledWith(12)
    expect(t.container.scrollTop).toBe(0)
  })

  // The scroller is asked through the component instance, which a production
  // bundle keeps, and not through a handle on the element that only a
  // development build defines: a table offering nothing but that handle takes
  // the fallback, as it would in production.
  it('does not reach the scroller through the element', () => {
    const scrollInView = vi.fn()
    const t = table({ accessor: false })
    t.container.__vnode = { component: { exposed: { scrollInView } } }
    expect(scrollVirtualRowIntoView(t, 3, { itemSize: 40 })).toBe(true)
    expect(scrollInView).not.toHaveBeenCalled()
    expect(t.container.scrollTop).toBe(120)
  })

  it('falls back to the row offset when the scroller offers no scrollInView', () => {
    const t = table()
    expect(scrollVirtualRowIntoView(t, 3, { itemSize: 40 })).toBe(true)
    expect(t.container.scrollTop).toBe(120)
  })

  it('does nothing without a table, a scroller, or a row', () => {
    expect(scrollVirtualRowIntoView(null, 3)).toBe(false)
    expect(scrollVirtualRowIntoView({ $el: { querySelector: () => null } }, 3)).toBe(false)
    const t = table({ scrollInView: vi.fn() })
    expect(scrollVirtualRowIntoView(t, null)).toBe(false)
    expect(scrollVirtualRowIntoView(t, -1)).toBe(false)
  })
})
