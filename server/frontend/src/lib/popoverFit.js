/**
 * Keep a PrimeVue Popover inside the window.
 *
 * PrimeVue places a popover below its anchor, or above it when there is no
 * room below; but one taller than the room above is pinned to the window's top
 * and runs off its bottom, and it is placed only once, when it opens, so a form
 * that grows (an evidence level picked, a structure asked for) grows past the
 * window's edge. The app's body does not scroll, so what falls outside is cut
 * off: the controls that save the form included.
 *
 * `usePopoverFit` places the popover again when it opens and whenever its
 * content changes size: below the anchor where it fits, above where it fits
 * there, and otherwise on the roomier side with its height capped to that
 * room, its content scrolling. The popover stays appended to the body, as
 * PrimeVue appends it; only its top and its height change.
 */
import { getCurrentInstance, onBeforeUnmount } from 'vue'

/** The least gap kept between a popover and the window's edge, in px. */
export const EDGE = 8

/**
 * Where a popover goes, in viewport coordinates.
 *
 * @param {object} args
 * @param {number} args.anchorTop the anchor's top edge
 * @param {number} args.anchorBottom the anchor's bottom edge
 * @param {number} args.height the popover's own height, uncapped
 * @param {number} args.viewportHeight the window's inner height
 * @param {number} [args.gutter] the gap between anchor and popover (its arrow)
 * @param {number} [args.edge] the gap kept to the window's edge
 * @param {'below'|'above'|null} [args.prefer] the side it is on now: it stays
 *   there while it fits, so a form that grows does not jump to the other side
 * @returns {{ side: 'below'|'above', top: number, maxHeight: number|null }}
 *   `top` is the popover's box top before its gutter margin (PrimeVue's margin
 *   adds the gutter below the anchor and takes it away above); `maxHeight` is
 *   null when it fits whole
 */
export function fitPlacement({
  anchorTop,
  anchorBottom,
  height,
  viewportHeight,
  gutter = 0,
  edge = EDGE,
  prefer = null
}) {
  const below = viewportHeight - anchorBottom - gutter - edge
  const above = anchorTop - gutter - edge
  const room = { below, above }
  const order = prefer === 'above' ? ['above', 'below'] : ['below', 'above']
  let side = order.find((candidate) => height <= room[candidate])
  let maxHeight = null
  if (!side) {
    side = above > below ? 'above' : 'below'
    // An anchor taller than the window leaves no room, never less.
    maxHeight = Math.max(0, room[side])
  }
  const shown = maxHeight ?? height
  const top = side === 'below' ? anchorBottom : anchorTop - shown
  return { side, top, maxHeight }
}

function gutterOf(container) {
  const value = parseFloat(getComputedStyle(container).getPropertyValue('--p-popover-gutter'))
  return Number.isFinite(value) ? value : 10
}

/**
 * Fit one PrimeVue Popover to the window.
 *
 * Bind `on` to the Popover (`v-on="fit.on"`); call `fit()` after anything that
 * re-places it by PrimeVue's own rule (`alignOverlay`).
 *
 * @param {import('vue').Ref} popover the Popover's component ref
 * @returns {{ on: { show: Function, hide: Function }, fit: Function }}
 */
export function usePopoverFit(popover) {
  let observer = null
  let side = null

  function parts() {
    const instance = popover.value
    const container = instance?.container
    const anchor = instance?.target
    if (!container || !anchor?.getBoundingClientRect) return null
    const content = container.querySelector('.p-popover-content') ?? container
    return { container, anchor, content }
  }

  function fit() {
    const found = parts()
    if (!found) return
    const { container, anchor, content } = found
    // Its own height, uncapped: the cap comes off before it is measured.
    container.style.maxHeight = ''
    content.style.overflowY = ''
    const rect = anchor.getBoundingClientRect()
    const placed = fitPlacement({
      anchorTop: rect.top,
      anchorBottom: rect.bottom,
      height: container.offsetHeight,
      viewportHeight: window.innerHeight,
      gutter: gutterOf(container),
      prefer: side
    })
    side = placed.side
    const flipped = placed.side === 'above'
    container.classList.toggle('p-popover-flipped', flipped)
    if (flipped) container.setAttribute('data-p-popover-flipped', 'true')
    else container.removeAttribute('data-p-popover-flipped')
    container.style.top = `${placed.top + window.scrollY}px`
    if (placed.maxHeight != null) {
      // The content scrolls inside the container, so the arrow, drawn on the
      // container, is not clipped with it.
      container.style.display = 'flex'
      container.style.flexDirection = 'column'
      container.style.maxHeight = `${placed.maxHeight}px`
      content.style.overflowY = 'auto'
      content.style.minHeight = '0'
      content.style.overscrollBehavior = 'contain'
    }
  }

  function show() {
    side = null
    fit()
    const found = parts()
    if (!found || typeof ResizeObserver === 'undefined') return
    observer?.disconnect()
    // Placing it again leaves the content the size it was observed at, so a
    // fit does not set the observer off again.
    observer = new ResizeObserver(() => fit())
    observer.observe(found.content)
    for (const child of found.content.children) observer.observe(child)
  }

  function hide() {
    observer?.disconnect()
    observer = null
  }

  if (getCurrentInstance()) onBeforeUnmount(hide)

  return { on: { show, hide }, fit }
}
