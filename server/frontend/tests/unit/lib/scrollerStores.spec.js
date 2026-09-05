import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useSampleScroller } from '@/lib/panes/PaneBrowserSample/stores/sampleScroller.js'
import { useIonScroller } from '@/lib/panes/PaneBrowserMatch/stores/ionScroller.js'
import { usePeakScroller } from '@/lib/panes/PaneBrowserPeak/stores/peakScroller.js'

// The three browser tables' scroll-to-row stores share one shape, so they are
// tested as one: each is handed a DataTable, finds the row's display index in
// the table's own processed order, and scrolls the virtual scroller to it.

/**
 * A DataTable stand-in shaped like PrimeVue's: the display order under
 * `processedData`, the VirtualScroller behind `getVirtualScrollerRef()`, and
 * the scroller's container under `$el`. `accessor: false` is a table with no
 * accessor to ask, which is what the offset fallback is for.
 */
function table(rows, { accessor = true } = {}) {
  const scrollInView = vi.fn()
  const container = { scrollTop: 0 }
  return {
    processedData: rows,
    $el: { querySelector: (selector) => (selector === '.p-virtualscroller' ? container : null) },
    ...(accessor ? { getVirtualScrollerRef: () => ({ scrollInView }) } : {}),
    container,
    scrollInView
  }
}

// The stores yield to the DOM (setTimeout 0) before they scroll.
const settled = () => new Promise((resolve) => setTimeout(resolve, 5))

const CASES = [
  {
    name: 'sample',
    use: useSampleScroller,
    key: 'sample_item_id',
    scroll: (store, id) => store.scrollToSample(id)
  },
  {
    name: 'ion',
    use: useIonScroller,
    key: 'target_ion_id',
    scroll: (store, id) => store.scrollToIon(id)
  },
  {
    name: 'peak',
    use: usePeakScroller,
    key: 'peak_id',
    scroll: (store, id) => store.scrollToPeak(id)
  }
]

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})

describe.each(CASES)('$name scroller', ({ use, key, scroll }) => {
  const rows = ['a', 'b', 'c', 'd'].map((id) => ({ [key]: id }))

  it("scrolls through the table's own scroller, to the row's display index", async () => {
    const t = table(rows)
    const store = use()
    store.bind(t, () => rows)

    await scroll(store, 'c')
    await settled()

    expect(t.scrollInView).toHaveBeenCalledWith(2)
    expect(t.container.scrollTop).toBe(0)
  })

  // The scroller is asked through the component instance, which a production
  // bundle keeps, and never through the `__vnode` handle on the element that
  // only a development build defines: a table offering nothing but that handle
  // takes the offset fallback, as it would in production.
  it('falls back to the row offset when the table has no scroller to ask', async () => {
    const t = table(rows, { accessor: false })
    t.container.__vnode = { component: { exposed: { scrollInView: t.scrollInView } } }
    const store = use()
    store.bind(t, () => rows)

    await scroll(store, 'd')
    await settled()

    expect(t.scrollInView).not.toHaveBeenCalled()
    expect(t.container.scrollTop).toBeGreaterThan(0)
  })

  it('scrolls nowhere for a row the table does not show', async () => {
    const t = table(rows)
    const store = use()
    store.bind(t, () => rows)

    await scroll(store, 'zz')
    await settled()

    expect(t.scrollInView).not.toHaveBeenCalled()
    expect(t.container.scrollTop).toBe(0)
  })
})
