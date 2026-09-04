/**
 * Scroll a PrimeVue DataTable's virtual scroller so the row at `index` is in
 * view.
 *
 * The same reach the sample table's scroller makes (`useSampleScroller`): the
 * VirtualScroller instance behind the table exposes `scrollInView`, which
 * scrolls the least distance that brings the row on screen; when it cannot be
 * reached, the container is scrolled to the row's offset outright. The index
 * is a DISPLAY index - the row's position in the order the table shows, which
 * with a pane-owned sort is the position in the array the table was given.
 *
 * @param {object|null|undefined} table - the DataTable component instance
 *   (a template ref)
 * @param {number|null|undefined} index - the row's display index
 * @param {{ itemSize?: number }} [options] - the row height the fallback
 *   multiplies by, the table's `virtualScrollerOptions.itemSize`
 * @returns {boolean} whether there was a scroller to scroll
 */
export function scrollVirtualRowIntoView(table, index, { itemSize = 35.5 } = {}) {
  if (index == null || index < 0) return false
  const container = table?.$el?.querySelector?.('.p-virtualscroller')
  if (!container) return false
  const instance = container.__vnode?.component?.exposed ?? container.__vnode?.component?.ctx
  if (typeof instance?.scrollInView === 'function') {
    instance.scrollInView(index)
    return true
  }
  container.scrollTop = index * itemSize
  return true
}
