import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { nextTick, defineComponent, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import Toast from 'primevue/toast'
import { useToast } from 'primevue/usetoast'

// The notification store registers a socket listener at setup.
vi.mock('@/api', () => ({
  api: {
    socket: { on: vi.fn(), id: 'test-sid' },
    http: { get: vi.fn(), post: vi.fn() }
  }
}))

import { useNotification } from '@/stores/ui/notification'
import { createToaster, MAX_TOASTS, SUMMARY_LIFE } from '@/lib/toaster'

const SUMMARY_DETAIL = 'Open the notification log to review them.'

/**
 * Stands in for PrimeVue's toast service. `remove` echoes a `close` back into
 * the toaster the way the Toast component does, so the tests exercise the same
 * feedback loop as the app.
 */
function createToastService() {
  const service = {
    added: [],
    removed: [],
    toaster: null,
    add: (message) => service.added.push(message),
    remove: (message) => {
      service.removed.push(message)
      service.toaster?.released({ message })
    }
  }
  return service
}

function setup() {
  const service = createToastService()
  const toaster = createToaster(service)
  service.toaster = toaster
  return { service, toaster }
}

const message = (n, severity = 'success') => ({
  severity,
  summary: `Thing ${severity}`,
  detail: `m${n}`,
  life: 3000
})

const shown = (service) => service.added.filter((m) => m.detail?.startsWith('m'))
const summaries = (service) => service.added.filter((m) => m.detail === SUMMARY_DETAIL)

describe('toaster', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows messages up to the cap', () => {
    const { service, toaster } = setup()

    for (let i = 0; i < MAX_TOASTS; i++) {
      toaster.show(message(i))
    }

    expect(shown(service)).toHaveLength(MAX_TOASTS)
    expect(summaries(service)).toHaveLength(0)
  })

  it('folds everything past the cap into a single summary', () => {
    const { service, toaster } = setup()

    for (let i = 0; i < MAX_TOASTS + 3; i++) {
      toaster.show(message(i))
    }

    expect(shown(service)).toHaveLength(MAX_TOASTS)

    expect(summaries(service)).toHaveLength(1)
    expect(summaries(service)[0].summary).toBe('3 more notifications')
    expect(summaries(service)[0].life).toBe(SUMMARY_LIFE)
  })

  it('bounds what is on screen no matter how many arrive', () => {
    const { service, toaster } = setup()

    for (let i = 0; i < 500; i++) {
      toaster.show(message(i))
    }

    expect(service.added).toHaveLength(MAX_TOASTS + 1)
    expect(shown(service)).toHaveLength(MAX_TOASTS)
  })

  it('updates the summary without taking it off screen', () => {
    const { service, toaster } = setup()

    for (let i = 0; i < 500; i++) {
      toaster.show(message(i))
    }

    // Replacing the summary would run a leave animation per suppressed
    // notification, and a tab that is not painting never finishes those.
    expect(service.removed).toHaveLength(0)
    expect(summaries(service)).toHaveLength(1)
    expect(summaries(service)[0].summary).toBe('495 more notifications')
  })

  it('singularises a lone suppressed notification', () => {
    const { service, toaster } = setup()

    for (let i = 0; i < MAX_TOASTS + 1; i++) {
      toaster.show(message(i))
    }

    expect(summaries(service).at(-1).summary).toBe('1 more notification')
  })

  it('gives the summary the severity of its worst entry', () => {
    const { service, toaster } = setup()

    for (let i = 0; i < MAX_TOASTS; i++) {
      toaster.show(message(i))
    }
    toaster.show(message(90, 'info'))
    toaster.show(message(91, 'error'))
    toaster.show(message(92, 'success'))

    expect(summaries(service).at(-1).severity).toBe('error')
  })

  it('shows new messages again once a slot is released', () => {
    const { service, toaster } = setup()

    for (let i = 0; i < MAX_TOASTS + 1; i++) {
      toaster.show(message(i))
    }
    expect(shown(service)).toHaveLength(MAX_TOASTS)

    toaster.released({ message: shown(service)[0] })
    toaster.show(message(99))

    expect(shown(service)).toHaveLength(MAX_TOASTS + 1)
  })

  it('does not release a slot twice', () => {
    const { service, toaster } = setup()

    for (let i = 0; i < MAX_TOASTS; i++) {
      toaster.show(message(i))
    }

    // Both `close` and `life-end` can arrive for the same toast.
    const first = shown(service)[0]
    toaster.released({ message: first })
    toaster.released({ message: first })

    toaster.show(message(100))
    toaster.show(message(101))

    expect(shown(service)).toHaveLength(MAX_TOASTS + 1)
  })

  it('reclaims a slot when the toast never reports back', () => {
    const { service, toaster } = setup()

    for (let i = 0; i < MAX_TOASTS; i++) {
      toaster.show(message(i))
    }
    toaster.show(message(50))
    expect(shown(service)).toHaveLength(MAX_TOASTS)

    vi.advanceTimersByTime(3000 + 1000)
    toaster.show(message(51))

    expect(shown(service)).toHaveLength(MAX_TOASTS + 1)
  })

  it('starts counting again after the summary expires', () => {
    const { service, toaster } = setup()

    for (let i = 0; i < MAX_TOASTS + 2; i++) {
      toaster.show(message(i))
    }
    expect(summaries(service).at(-1).summary).toBe('2 more notifications')

    toaster.released({ message: summaries(service).at(-1) })
    toaster.show(message(200))

    expect(summaries(service).at(-1).summary).toBe('1 more notification')
  })

  it('counts every suppressed notification', () => {
    const { service, toaster } = setup()

    for (let i = 0; i < MAX_TOASTS + 4; i++) {
      toaster.show(message(i))
    }

    expect(summaries(service)).toHaveLength(1)
    expect(summaries(service)[0].summary).toBe('4 more notifications')
  })

  it('ignores a release for an unknown toast', () => {
    const { service, toaster } = setup()

    for (let i = 0; i < MAX_TOASTS; i++) {
      toaster.show(message(i))
    }

    toaster.released({ message: { id: 'not-ours' } })
    toaster.released({})
    toaster.released()

    toaster.show(message(300))

    expect(shown(service)).toHaveLength(MAX_TOASTS)
  })
})

/**
 * The cap only holds if the Toast component reports every departure back to the
 * toaster, so this drives the real component the way App.vue wires it. Timers
 * are frozen throughout: that is the case the cap exists for, a tab whose
 * dismissal timers are throttled while notifications keep arriving.
 */
describe('toaster driving the real Toast component', () => {
  let wrapper

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })

  afterEach(() => {
    wrapper?.unmount()
    vi.useRealTimers()
  })

  function mountToaster(store) {
    const Harness = defineComponent({
      setup() {
        const toaster = createToaster(useToast())
        store.on('*', (notification) => {
          if (notification === null || notification.status === 'pending') return
          toaster.show({
            severity: notification.status,
            summary: notification.type,
            detail: notification.message,
            life: 3000
          })
        })
        return () =>
          h(Toast, {
            position: 'bottom-right',
            onClose: toaster.released,
            'onLife-end': toaster.released
          })
      }
    })

    return mount(Harness, {
      global: { plugins: [PrimeVue, ToastService] },
      attachTo: document.body
    })
  }

  it('keeps a burst of notifications bounded on screen', async () => {
    const store = useNotification()
    wrapper = mountToaster(store)
    await nextTick()

    for (let i = 0; i < 200; i++) {
      // Socket messages arrive in separate tasks, so each one reaches the
      // watcher; pushing synchronously would collapse them into a single fire.
      store.push({ type: 'process_sample_item', status: 'success', message: `done ${i}` })
      await nextTick()
    }
    await nextTick()

    expect(document.querySelectorAll('.p-toast-message')).toHaveLength(MAX_TOASTS + 1)
    expect(store.log).toHaveLength(200)
    // The summary is edited in place, so its count has to reach the rendered
    // DOM without the message being replaced.
    expect(document.body.textContent).toContain(`${200 - MAX_TOASTS} more notifications`)
  })

  it('drains once the dismissal timers run again', async () => {
    const store = useNotification()
    wrapper = mountToaster(store)
    await nextTick()

    for (let i = 0; i < 20; i++) {
      store.push({ type: 'process_sample_item', status: 'success', message: `done ${i}` })
      await nextTick()
    }
    expect(document.querySelectorAll('.p-toast-message').length).toBeGreaterThan(0)

    vi.advanceTimersByTime(SUMMARY_LIFE + 1000)
    await nextTick()

    expect(document.querySelectorAll('.p-toast-message')).toHaveLength(0)
  })
})
