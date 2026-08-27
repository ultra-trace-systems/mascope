import { describe, it, expect, vi, beforeEach } from 'vitest'
import { reactive, nextTick, onMounted } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import Toast from 'primevue/toast'
import { useToast } from 'primevue/usetoast'
import { style as primevueToastStyle } from '@primeuix/styles/toast'

// A batch operation reports what it could not do as one line per item: a
// heading, then "<name>: <reason>" for each. Both surfaces that show a
// notification have to render those breaks, or the entries run together - the
// reasons end in no punctuation, so a collapsed message leaves the reader
// nothing at all between one entry and the next.
//
// This covers both surfaces of that one behaviour, which is why it sits with
// the other cross-cutting specs rather than under a mirrored src path.

// Composed the way match_controller and calibration_controller compose it.
const COMPOSED = [
  'Rematched 1 of 3 batches. Failed to rematch 2 batch(es):',
  'QC batch A: No m/z calibration for 3 samples',
  'QC batch B: Target collection is empty'
].join('\n')

const ONE_LINE = 'Sample rematched.'

const mocks = vi.hoisted(() => ({ app: null }))
vi.mock('@/stores', () => ({ useApp: () => mocks.app }))

import NotificationPane from '@/lib/toolbars/ToolbarAppFilters/SidebarMenu/NotificationPane.vue'

// Shaped like a real log entry: the store stamps its own `id` on every
// notification and keeps the `process_id` the server sent.
const logEntry = (message) => ({
  id: 'n1',
  process_id: 'p1',
  type: 'rematch_batches',
  status: 'warning',
  message,
  timestamp: new Date('2026-08-27T09:00:00.000Z')
})

const mountPane = (message) => {
  mocks.app = reactive({
    ui: {
      help: { set: () => {}, directive: () => ({}) },
      notification: { log: [logEntry(message)] }
    }
  })
  // Attached on purpose: happy-dom resolves a computed style only for an
  // element that is in the document.
  const wrapper = mount(NotificationPane, {
    global: { plugins: [PrimeVue] },
    attachTo: document.body
  })
  return wrapper.findAll('p').find((p) => p.text().includes(message.split('\n')[0]))
}

describe('the notification pane', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    document.body.innerHTML = ''
  })

  it('keeps a composed message on the lines it was composed as', () => {
    const rendered = mountPane(COMPOSED)

    // The breaks survive the trip into the DOM...
    expect(rendered.text()).toContain('\n')
    // ...and the element is told to render them, which is the whole fix. At
    // the default `normal` these three lines collapse into one paragraph
    // reading "...2 batch(es): QC batch A: ... QC batch B: ...".
    expect(getComputedStyle(rendered.element).whiteSpace).toBe('pre-line')
  })

  it('shows an ordinary one-line notification word for word', () => {
    // `pre-line` differs from `normal` only on newlines - it collapses runs of
    // spaces and wraps on width just the same - so the far more common
    // one-line notification has to come out untouched.
    expect(mountPane(ONE_LINE).text()).toBe(ONE_LINE)
  })
})

// The toast is the other surface: App.vue hands the same message to PrimeVue
// as the toast `detail`. PrimeVue's own component stylesheet already sets
// `white-space: pre-line` on `.p-toast`, which `.p-toast-detail` inherits, so
// toasts have been rendering the lines all along and need no rule of ours.
// Measured in Chromium against the app's real PrimeVue config: deleting that
// one rule through the CSSOM flips the computed value on `.p-toast-detail`
// from `pre-line` to `normal`. Load-bearing and third-party at once - so both
// halves of the chain are pinned here, and a PrimeVue upgrade that drops
// either fails in CI instead of quietly restoring the run-on.
describe('the notification toast', () => {
  it('hands the detail element the message with its breaks intact', async () => {
    document.body.innerHTML = ''
    const Harness = {
      components: { Toast },
      setup() {
        const toast = useToast()
        // After mount, so the Toast container is listening - the same order
        // App.vue produces, where the message arrives on a socket event.
        onMounted(() =>
          toast.add({ severity: 'warn', summary: 'Rematch batches warning', detail: COMPOSED })
        )
      },
      template: '<Toast />'
    }

    mount(Harness, { global: { plugins: [PrimeVue, ToastService] }, attachTo: document.body })
    await nextTick()

    const detail = document.querySelector('.p-toast-detail')
    expect(detail).not.toBeNull()
    expect(detail.textContent).toBe(COMPOSED)
  })

  it('still ships the rule that makes those breaks visible', () => {
    const toastRule = primevueToastStyle.match(/\.p-toast\s*\{[^}]*\}/)

    expect(toastRule).not.toBeNull()
    expect(toastRule[0]).toMatch(/white-space:\s*pre-line/)
  })
})
