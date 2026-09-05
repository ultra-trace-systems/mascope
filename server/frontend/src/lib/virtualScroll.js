/**
 * Scroll a PrimeVue DataTable's virtual scroller so the row at `index` is in
 * view.
 *
 * The VirtualScroller instance behind the table exposes `scrollInView`, which
 * scrolls the least distance that brings the row on screen. It is reached
 * through the DataTable's own accessor, `getVirtualScrollerRef()`: a method of
 * the component instance, so it is there in a production bundle. (The `__vnode`
 * handle on the scroller's element is not - Vue defines it only in development
 * builds, or with the devtools flag on - so a reach through it works on a dev
 * server and quietly takes the fallback everywhere else.) When there is no
 * scroller to ask, the container is scrolled to the row's offset outright,
 * which puts the row at the top of the viewport rather than the least distance
 * away. The index is a DISPLAY index - the row's position in the order the
 * table shows, which with a pane-owned sort is the position in the array the
 * table was given.
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
  const scroller = table.getVirtualScrollerRef?.()
  if (typeof scroller?.scrollInView === 'function') {
    scroller.scrollInView(index)
    return true
  }
  container.scrollTop = index * itemSize
  return true
}
