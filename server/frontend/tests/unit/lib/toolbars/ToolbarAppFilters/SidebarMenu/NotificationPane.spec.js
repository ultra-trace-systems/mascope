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
// Nothing kept: the pane's kept-notification section has its own spec.
const inbox = reactive({ items: [], sorted: [], unread: [] })

vi.mock('@/stores', () => ({
  useApp: () => ({
    ui: { notification, inbox, help: { set: vi.fn(), directive: () => ({}) } }
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

  // Headed as its toast was (src/lib/toastTitles.js), so the log and the
  // toast name one event alike, and a search for the heading finds it.
  it('heads an entry as its toast was headed', () => {
    notification.log = [{ ...entry('a'), type: 'rematch_batches', status: 'warning' }]
    const wrapper = mountPane()

    expect(wrapper.get('h4').text()).toBe('Match refresh warning')
  })

  // Coloured as its toast was: the store reads the severity off the full
  // notification, which the log entry no longer carries.
  it('colours an entry by the severity the store read', () => {
    notification.log = [
      { ...entry('nothing'), type: 'match_compositions_by_mz', severity: 'info' },
      { ...entry('older'), type: 'rematch_batches', status: 'warning' }
    ]
    const wrapper = mountPane()
    const [empty, older] = wrapper.findAll('.entry')

    expect(empty.attributes('severity')).toBe('info')
    expect(older.attributes('severity')).toBe('warn')
  })

  it('finds an entry by that heading', async () => {
    notification.log = [
      { ...entry('one'), type: 'rematch_batches', status: 'warning' },
      { ...entry('two'), type: 'shared_link', status: 'success' }
    ]
    const wrapper = mountPane()
    expect(wrapper.findAll('.entry')).toHaveLength(2)

    wrapper.vm.log.query = 'Match refresh'
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.entry')).toHaveLength(1)
    expect(wrapper.get('h4').text()).toBe('Match refresh warning')
  })

  // A mapped heading says "failed" where the status says "error"; the raw
  // status stays in the search, so "error" still finds every failure.
  it('still finds a failure by its status word', async () => {
    notification.log = [
      { ...entry('upload'), type: 'sample_file_upload', status: 'error' },
      { ...entry('link'), type: 'shared_link', status: 'success' }
    ]
    const wrapper = mountPane()

    wrapper.vm.log.query = 'error'
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.entry')).toHaveLength(1)
    expect(wrapper.get('h4').text()).toBe('Upload failed')
  })
})
