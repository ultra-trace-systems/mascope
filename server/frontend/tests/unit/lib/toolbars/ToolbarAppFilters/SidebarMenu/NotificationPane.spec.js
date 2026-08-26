import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import PrimeVue from 'primevue/config'

// The pane only reads the notification feed off the aggregate store; stub it so
// the test does not pull in the whole store tree (and its socket/API side
// effects). Pinia still has to be active for the pane's own sidebar-menu store.
const clearLog = vi.fn()
const notification = reactive({ log: [], clearLog })

vi.mock('@/stores', () => ({
  useApp: () => ({
    ui: { notification, help: { set: vi.fn(), directive: () => ({}) } }
  })
}))

import NotificationPane from '@/lib/toolbars/ToolbarAppFilters/SidebarMenu/NotificationPane.vue'

const STUBS = {
  ScrollPanel: { template: '<div><slot /></div>' },
  Message: { template: '<div class="entry"><slot /></div>' },
  IconField: { template: '<div><slot /></div>' },
  InputIcon: { template: '<div><slot /></div>' },
  InputText: true
}

const noop = {}

const mountPane = () =>
  mount(NotificationPane, {
    global: { plugins: [PrimeVue], stubs: STUBS, directives: { tooltip: noop, ripple: noop } }
  })

const entry = (message) => ({
  id: message,
  timestamp: new Date(),
  type: 'mz_fit',
  status: 'success',
  message
})

describe('NotificationPane', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    notification.log = []
  })

  it('clears the feed from the header button', async () => {
    notification.log = [entry('a')]
    const wrapper = mountPane()

    const clear = wrapper.get('button[aria-label="Clear notifications"]')
    expect(clear.attributes('disabled')).toBeUndefined()
    await clear.trigger('click')

    expect(clearLog).toHaveBeenCalledOnce()
  })

  it('disables the clear button when the feed is empty', () => {
    const wrapper = mountPane()

    expect(
      wrapper.get('button[aria-label="Clear notifications"]').attributes('disabled')
    ).toBeDefined()
  })
})
