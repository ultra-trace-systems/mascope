<script setup>
import { ref, computed, watchEffect } from 'vue'

import { useMagicKeys, watchDebounced } from '@vueuse/core'
import { useFloating, arrow, offset } from '@floating-ui/vue'

import { useApp } from '@/stores'

const app = useApp()

const targetEl = ref()
const popoverEl = ref()
const arrowEl = ref()

const placement = ref()

const { floatingStyles, x, y, middlewareData } = useFloating(targetEl, popoverEl, {
  placement,
  strategy: 'fixed',
  middleware: [offset(10), arrow({ element: arrowEl })]
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
    placement.value = card?.placement.replace('_', '-') ?? 'bottom'
    displayed.value = card
    targetEl.value = card.element
    visible.value = true
  } else {
    hide()
  }
})

// Invisible extension of the popover's hover region towards its target,
// covering the offset gap so the pointer never passes through untracked space
// on its way in.
const BRIDGE = '12px'
const bridgeStyle = computed(
  () =>
    ({
      top: { left: 0, right: 0, bottom: `-${BRIDGE}`, height: BRIDGE },
      bottom: { left: 0, right: 0, top: `-${BRIDGE}`, height: BRIDGE },
      left: { top: 0, bottom: 0, right: `-${BRIDGE}`, width: BRIDGE },
      right: { top: 0, bottom: 0, left: `-${BRIDGE}`, width: BRIDGE }
    })[(placement.value ?? 'bottom').split('-')[0]]
)

const border = '1px solid var(--p-panel-border-color)'

// position box
watchEffect(() => {
  if (popoverEl.value) {
    Object.assign(popoverEl.value.style, {
      top: `${y.value}px`,
      left: `${x.value}px`
    })
  }
})

// position arrow
watchEffect(() => {
  const { arrow } = middlewareData.value
  if (arrow && popoverEl.value) {
    const [position, alignment] = placement.value.split('-')
    let yOffset = 0
    if (alignment == 'start') {
      yOffset = -popoverEl.value.offsetHeight + 2 * arrowEl.value.offsetHeight
    } else if (alignment == 'end') {
      yOffset = popoverEl.value.offsetHeight - 2 * arrowEl.value.offsetHeight
    }

    Object.assign(
      arrowEl.value.style,
      {
        top: {
          left: `${arrow.x}px`,
          top: `${popoverEl.value.offsetHeight - arrowEl.value.offsetHeight / 2}px`,
          border,
          'border-top': 'none',
          'border-left': 'none'
        },
        right: {
          top: `${arrow.y + yOffset}px`,
          left: `${-arrowEl.value.offsetHeight / 2}px`,
          border,
          'border-top': 'none',
          'border-right': 'none'
        },
        bottom: {
          left: `${arrow.x}px`,
          top: `${-arrowEl.value.offsetHeight / 2}px`,
          border,
          'border-bottom': 'none',
          'border-right': 'none'
        },
        left: {
          top: `${arrow.y + yOffset}px`,
          left: `${popoverEl.value.offsetWidth - arrowEl.value.offsetWidth / 2 - 1}px`,
          border,
          'border-bottom': 'none',
          'border-left': 'none'
        }
      }[position]
    )
  }
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
  font-size: 0.85rem;
  text-decoration: underline;
  color: inherit;
}
</style>
