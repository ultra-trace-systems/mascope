import { describe, it, expect, vi } from 'vitest'

import { scrollVirtualRowIntoView } from '@/lib/virtualScroll'

/** A DataTable stand-in whose virtual scroller exposes what PrimeVue's does. */
function table({ scrollInView = null } = {}) {
  const container = {
    scrollTop: 0,
    __vnode: { component: { exposed: scrollInView ? { scrollInView } : {} } }
  }
  return {
    container,
    $el: { querySelector: (selector) => (selector === '.p-virtualscroller' ? container : null) }
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

  it('falls back to the row offset when it does not', () => {
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
