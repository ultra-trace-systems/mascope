import { ref, computed, watchEffect } from 'vue'
import { defineStore } from 'pinia'

import { useMouseInElement, watchDebounced } from '@vueuse/core'

import { genId } from '@/lib/utils'

// Base path where the bundled MkDocs user documentation is served (nginx serves
// the static site at /docs/ -- see server/frontend/nginx.conf). Build a link
// into it with docUrl('how-it-works/matching/') and pass the result as the
// `doc` option of a help card to add a "Learn more" link to its popover.
export const DOCS_BASE = '/docs/'
export const docUrl = (path = '') => DOCS_BASE + String(path).replace(/^\/+/, '')

export const useHelp = defineStore('app.ui.help', () => {
  const active = ref(false)
  const layer = ref(null)
  const cards = ref([])
  const roots = ref([])
  const current = ref()
  const event = ref()

  // Help-card bodies rendered from the shared docs snippets (keyed by helpKey;
  // see tooling/docs/build_help_content.py). Fetched once from the docs served
  // alongside the app. In dev, vite proxies /docs/ to a local `mkdocs serve`
  // (see vite.config.js); mkdocs serve does not emit help-content.json, so
  // this stays empty there and helpKey cards fall back to their title plus
  // the "Learn more" link.
  const content = ref({})
  const loadContent = async () => {
    if (typeof fetch === 'undefined') return
    try {
      const res = await fetch(docUrl('help-content.json'), { cache: 'no-cache' })
      if (res.ok) content.value = await res.json()
    } catch {
      // docs (and this JSON) are not served here; leave content empty
    }
  }
  loadContent()

  const toggle = () => {
    active.value = !active.value
  }
  watchEffect(() => {
    if (active.value) {
      document.documentElement.classList.add('disable-tooltips')
    } else {
      document.documentElement.classList.remove('disable-tooltips')
    }
  })

  const set = (current) => {
    layer.value = current
  }

  const register = (element, args) => {
    const { isOutside } = useMouseInElement(element)
    cards.value.push({ element, isOutside, ...args })
  }

  // hook into HTML elmenents:
  // v-help accepts either a string (the message) or an object: { message, doc }
  //   for an inline card, or { title, helpKey, doc } for a docs-sourced card
  //   whose body is the snippet named by helpKey (see resolveMessage) -- the
  //   same shapes the pt API takes. `doc` is an optional URL the popover links
  //   to ("Learn more"). Keep popover text short and put longer explanations
  //   in the docs site, linked via `doc`.
  const directive = (layer = 'default') => ({
    mounted: (element, { value, modifiers }) => {
      const [placement] = Object.keys(modifiers)
      const args = value && typeof value === 'object' ? value : { message: value }
      register(element, { ...args, placement, layer })
    }
  })
  // hook into components:
  const pt = (args) => {
    const rootId = `help-${genId(8)}`
    roots.value.push([rootId, args])
    return {
      root: { id: rootId },
      hooks: {
        onMounted: () => {
          const element = document.getElementById(rootId)
          console.debug('🛟 [help] registering element', rootId, { args, element })
          register(element, args)
        }
      }
    }
  }

  const positions = ['right', 'left', 'top', 'bottom']
  const alignments = [null, 'start', 'end']

  const ptApi = {}
  positions.forEach((position) => {
    alignments.forEach((alignment) => {
      // create an API that combines position and alignment
      const placement = [position, alignment].filter((x) => x !== null).join('_')
      // The first argument is the message string for legacy inline cards, or an
      // options object ({ helpKey, title, doc }) for docs-sourced cards. layer
      // defaults to 'default' before the spreads, so callers can add options
      // without restating it -- a card with layer undefined never matches the
      // active layer and so never shows.
      ptApi[placement] = (messageOrOptions, options = {}) => {
        const base =
          typeof messageOrOptions === 'string'
            ? { message: messageOrOptions }
            : { ...(messageOrOptions ?? {}) }
        return pt({ placement, layer: 'default', ...base, ...options })
      }
    })
  })

  const buffer = computed(() => {
    // Only consider cards whose element is still in the DOM
    const active = cards.value.filter(
      (card) =>
        card.layer == (layer.value ?? 'default') &&
        card.element &&
        document.body.contains(card.element)
    )
    // filter to select cards containing the mouse
    const hovered = active.filter(({ isOutside }) => !isOutside)
    // select the lowest element in the containment hierarchy
    return hovered.sort(containment)[0]
  })
  watchDebounced(
    buffer,
    (card) => {
      current.value = card
    },
    { debounce: 300 }
  )

  // Render a card's popover body: an inline message wins (legacy cards);
  // otherwise the card's title plus the docs snippet named by helpKey. A
  // function (not a computed on `current`) so the popover can keep rendering a
  // card it has "pinned" open even after the store's `current` has cleared.
  const resolveMessage = (card) => {
    if (!card) return ''
    if (card.message) return card.message
    const title = card.title ? `<h1>${card.title}</h1>` : ''
    const body = card.helpKey ? (content.value[card.helpKey] ?? '') : ''
    return title + body
  }

  return {
    active,
    cards,
    current,
    resolveMessage,
    event,
    toggle,
    set,
    layer,
    docUrl,
    loadContent,
    // hooks
    directive,
    pt,
    ...ptApi
  }
})

function containment(a, b) {
  if (a?.element && b?.element) {
    if (a.element.contains(b.element)) {
      return 1
    }
    if (b.element.contains(a.element)) {
      return -1
    }
  }
  return 0
}
