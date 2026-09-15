<script setup>
import { ref, computed, watchEffect } from 'vue'

import { useMagicKeys, watchDebounced } from '@vueuse/core'
import { useFloating, arrow, offset, flip, shift, size } from '@floating-ui/vue'

import { useApp } from '@/stores'

const app = useApp()

const targetEl = ref()
const popoverEl = ref()
const arrowEl = ref()

const placement = ref()

// Gap kept between the popover and the edge of the viewport, so a card that has
// been flipped or shifted into view does not end up flush against it.
const VIEWPORT_PADDING = 8

// size() writes a max-height onto the popover, and that capped height is what
// flip() measures when the next card opens: a cap left over from a shorter card
// would make a tall one look as though it fits where it stands, so it would be
// squeezed there instead of moved to the side with the room for it.
const clearCap = () => {
  if (popoverEl.value) popoverEl.value.style.maxHeight = ''
}

// `placement` below is the card's *requested* side; `resolvedPlacement` is where
// the popover actually ended up once flip() has had its say, and is what the
// arrow and the hover bridge have to follow.
const {
  floatingStyles,
  placement: resolvedPlacement,
  middlewareData
} = useFloating(targetEl, popoverEl, {
  placement,
  strategy: 'fixed',
  // The order is load-bearing. flip() has to pick a side before size() measures
  // how much room that side has, or a card that does not fit is shrunk where it
  // stands and flip() then finds nothing overflowing to move. size() reads
  // shift()'s data to tell whether an axis was free to move, and arrow() runs
  // last so its offset describes wherever the rest of the chain left the box.
  middleware: [
    offset(10),
    // A card whose target is a whole pane fits on neither side of it, and
    // squeezing it into the sliver that leaves is no better than the crop it
    // replaces. Allowing the other axis lets such a card move beside its target
    // instead, where shift() has the full height of the viewport to slide it
    // along.
    flip({ padding: VIEWPORT_PADDING, fallbackAxisSideDirection: 'start' }),
    shift({ padding: VIEWPORT_PADDING }),
    size({
      padding: VIEWPORT_PADDING,
      apply({ availableHeight, elements }) {
        // Cap the popover, not its body: the body is a flex child that scrolls,
        // so the padding, the border and the "Learn more" link keep their space
        // without this having to know how tall any of them are. The app's global
        // border-box sizing is what makes availableHeight the whole box.
        elements.floating.style.maxHeight = `${Math.max(0, availableHeight)}px`
      }
    }),
    arrow({ element: arrowEl })
  ]
})

const visible = ref(false)

// The card the popover is currently showing. Held locally (rather than reading
// app.ui.help.current directly) so that when the pointer moves onto the popover
// -- which clears the store's `current` ~300ms later -- the body and the "Learn
// more" link stay put instead of blanking out.
const displayed = ref(null)
const displayedMessage = computed(() => app.ui.help.resolveMessage(displayed.value))

// Keep the popover open while the pointer is over it, so its "Learn more" link
// stays reachable. `pinned` is driven by the popover's own enter/leave events;
// the bridge element below extends that region across the gap to the target.
const pinned = ref(false)
const onPopoverLeave = () => {
  pinned.value = false
}

const hide = () => {
  visible.value = false
  displayed.value = null
  // The popover is unmounted by v-if, which fires no mouseleave, so clear the
  // pin here or the next card would be frozen out.
  pinned.value = false
}

watchEffect(() => {
  const card = app.ui.help.current
  const helpActive = app.ui.help.active
  // Pointer on the popover: hold the card it opened with. Hover detection is
  // geometric, so an element this popover covers still counts as hovered and
  // would otherwise swap the card away mid-reach for "Learn more".
  if (helpActive && pinned.value) return
  if (helpActive && card) {
    // Only on a genuine change of card: this effect also reruns when the pointer
    // leaves the popover, and dropping the cap then would be a pointless round
    // trip through the whole chain.
    if (card !== displayed.value) clearCap()
    // The store spells a placement with an underscore, because that is the only
    // spelling a Vue modifier allows. A card registered without a modifier has
    // no placement at all, so ask for the default rather than throwing on it.
    placement.value = card.placement?.replace('_', '-') ?? 'bottom'
    displayed.value = card
    targetEl.value = card.element
    visible.value = true
  } else {
    hide()
  }
})

// A placement is a side plus an optional alignment ("bottom", "bottom-end");
// everything below cares only about the side.
const side = (value) => (value ?? 'bottom').split('-')[0]

// Invisible extension of the popover's hover region towards its target,
// covering the offset gap so the pointer never passes through untracked space
// on its way in. It follows the resolved placement: a card that asked to sit
// above its target but was flipped below it has the gap on the other side, and
// a bridge left on the old edge would strand the pointer.
const BRIDGE = '12px'
const bridgeStyle = computed(
  () =>
    ({
      top: { left: 0, right: 0, bottom: `-${BRIDGE}`, height: BRIDGE },
      bottom: { left: 0, right: 0, top: `-${BRIDGE}`, height: BRIDGE },
      left: { top: 0, bottom: 0, right: `-${BRIDGE}`, width: BRIDGE },
      right: { top: 0, bottom: 0, left: `-${BRIDGE}`, width: BRIDGE }
    })[side(resolvedPlacement.value)]
)

const border = '1px solid var(--p-panel-border-color)'

// The edge the arrow sits on is the opposite of where the popover ended up: a
// card placed above its target carries its arrow on its own bottom edge.
const ARROW_SIDES = { top: 'bottom', right: 'left', bottom: 'top', left: 'right' }

// Only the two borders facing away from the popover are drawn, so the rotated
// square reads as a point rather than a diamond.
const ARROW_BORDERS = {
  top: { borderTop: 'none', borderLeft: 'none' },
  right: { borderTop: 'none', borderRight: 'none' },
  bottom: { borderBottom: 'none', borderRight: 'none' },
  left: { borderBottom: 'none', borderLeft: 'none' }
}

// position arrow
watchEffect(() => {
  const { arrow } = middlewareData.value
  if (!arrow || !arrowEl.value) return

  const position = side(resolvedPlacement.value)
  const edge = ARROW_SIDES[position]
  // Half the square hangs past the edge, and the rotation turns that half into
  // the visible point. floating-ui gives the along-the-edge offset already
  // corrected for any shift, so nothing here re-derives it from the box size --
  // which also means a popover that size() has capped still gets it right.
  const overhang =
    edge == 'top' || edge == 'bottom' ? arrowEl.value.offsetHeight : arrowEl.value.offsetWidth

  // Only one of x/y is ever reported, on whichever axis the placement runs
  // along, so the other three sides are cleared: a card that flips would
  // otherwise keep the offsets it was given on the edge it used to sit on. The
  // two axes are perpendicular, so `edge` only ever fills in a cleared side.
  Object.assign(arrowEl.value.style, {
    top: arrow.y == null ? '' : `${arrow.y}px`,
    left: arrow.x == null ? '' : `${arrow.x}px`,
    right: '',
    bottom: '',
    [edge]: `${-overhang / 2}px`,
    border,
    ...ARROW_BORDERS[position]
  })
})

// keybindings
const keys = useMagicKeys()
const combo = keys['alt+h']
watchDebounced(
  combo,
  () => {
    app.ui.help.toggle()
  },
  { debounce: 200 }
)
</script>

<template>
  <div
    ref="popoverEl"
    v-if="visible"
    :style="floatingStyles"
    class="help-popover"
    @mouseenter="pinned = true"
    @mouseleave="onPopoverLeave"
  >
    <div class="help-content" v-html="displayedMessage" />
    <a
      v-if="displayed?.doc"
      class="help-learn-more"
      :href="displayed.doc"
      target="_blank"
      rel="noopener"
    >
      Learn more
    </a>
    <div ref="arrowEl" class="help-popover-arrow"></div>
    <div class="help-popover-bridge" :style="bridgeStyle"></div>
  </div>
</template>

<style scoped>
.help-popover {
  position: fixed;
  padding: 1rem;
  z-index: 9999;
  border-radius: 0.5rem;
  border: 1px solid var(--p-panel-border-color);
  /* size() caps this box's height when a tall card would not otherwise fit.
     Laying it out as a column is what lets .help-content give up the excess and
     scroll while the padding and the "Learn more" link keep theirs. Overflow
     stays visible so the arrow and the bridge, which hang outside the box, are
     not clipped -- which is also why the body scrolls rather than this. */
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}

.help-popover-arrow {
  position: absolute;
  width: 15px;
  height: 15px;
  transform: rotate(45deg);
}

.help-popover-bridge {
  position: absolute;
}
</style>

<style>
html:not(.darkmode) {
  .help-popover {
    color: var(--p-surface-200);
    background: var(--p-surface-800);
  }

  .help-popover-arrow {
    background: var(--p-surface-800);
  }
}
html.darkmode {
  .help-popover {
    color: var(--p-surface-800);
    background: var(--p-surface-200);
  }
  .help-popover-arrow {
    background: var(--p-surface-200);
  }
}
.help-content {
  max-width: 300px;
  /* The body is the one child that can lose height, so it is what scrolls once
     size() has capped the popover. A flex item will not shrink below its content
     without min-height: 0, and without that the scrollbar never appears. */
  overflow-y: auto;
  min-height: 0;

  h1 {
    font-size: 1.1rem;
    margin-top: 0.5rem;
  }
  h2 {
    font-size: 1rem;
    margin-top: 0.3rem;
  }
  h3 {
    font-size: 1rem;
    margin-top: 0.1rem;
    font-weight: normal;
    font-style: italic;
  }
}
.help-learn-more {
  display: inline-block;
  margin-top: 0.5rem;
  /* Flex would otherwise take this link's share of a capped card's shortfall out
     of its height, on the one control the popover exists to keep reachable. */
  flex-shrink: 0;
  font-size: 0.85rem;
  text-decoration: underline;
  color: inherit;
}
</style>
