import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import PrimeVue from 'primevue/config'

// The kept notifications a person has not dealt with yet, at the top of the
// notifications pane. "Show files" opens Raw files on the files a digest
// names: its instrument, its status, and a window reaching back to the oldest.

const mocks = vi.hoisted(() => ({ app: null }))
vi.mock('@/stores', () => ({ useApp: () => mocks.app }))

import NotificationInbox from '@/lib/toolbars/ToolbarAppFilters/SidebarMenu/NotificationInbox.vue'
import { useSidebarMenu } from '@/lib/toolbars/ToolbarAppFilters/SidebarMenu/state.js'

const digest = (overrides = {}) => ({
  notification_id: 'n1',
  kind: 'needs_chemistry',
  severity: 'warning',
  instrument: 'Orbi-1',
  message: '2 files from Orbi-1 need a chemistry.',
  count: 2,
  payload: {
    status: 'needs_chemistry',
    files: [
      {
        sample_file_id: 'sf-2',
        filename: 'Orbi-1_b.raw',
        datetime_utc: '2026-09-21T10:00:00+00:00',
        detail: 'No ionization mode tokens found for file Orbi-1_b.raw.'
      },
      {
        sample_file_id: 'sf-1',
        filename: 'Orbi-1_a.raw',
        datetime_utc: '2026-09-20T08:00:00+00:00',
        detail: null
      }
    ]
  },
  updated_utc: '2026-09-21T10:05:00+00:00',
  read_utc: null,
  resolved_utc: null,
  ...overrides
})

const setup = (items) => {
  const unread = items.filter((item) => !item.read_utc)
  mocks.app = reactive({
    ui: {
      inbox: {
        items,
        sorted: items,
        unread,
        markRead: vi.fn(),
        markAllRead: vi.fn()
      },
      tab: { active: 'batch' }
    },
    data: {
      instrument: { focus: vi.fn() },
      acquisition: { processingStatus: null, time: { range: { min: null, max: null } } }
    }
  })
  return mount(NotificationInbox, {
    global: { plugins: [PrimeVue], directives: { tooltip: {}, ripple: {} } }
  })
}

const button = (wrapper, label) => wrapper.findAll('button').find((b) => b.text() === label)

describe('NotificationInbox', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('shows nothing when nothing is kept', () => {
    const wrapper = setup([])

    expect(wrapper.find('section').exists()).toBe(false)
  })

  it('shows what a digest is about and its latest file', () => {
    const wrapper = setup([digest()])

    expect(wrapper.text()).toContain('Needs a chemistry · Orbi-1')
    expect(wrapper.text()).toContain('2 files from Orbi-1 need a chemistry.')
    expect(wrapper.text()).toContain('Latest: Orbi-1_b.raw')
    expect(wrapper.text()).toContain('No ionization mode tokens found for file Orbi-1_b.raw.')
  })

  it('marks a digest read', async () => {
    const wrapper = setup([digest()])

    await button(wrapper, 'Mark read').trigger('click')

    expect(mocks.app.ui.inbox.markRead).toHaveBeenCalledWith(['n1'])
  })

  it('offers nothing to mark on a digest already read', () => {
    const wrapper = setup([digest({ read_utc: '2026-09-21T11:00:00+00:00' })])

    expect(button(wrapper, 'Mark read')).toBeUndefined()
    expect(button(wrapper, 'Mark all read').attributes('disabled')).toBeDefined()
  })

  it('says when a digest is resolved', () => {
    const wrapper = setup([digest({ resolved_utc: '2026-09-21T12:00:00+00:00' })])

    expect(wrapper.text()).toContain('resolved')
  })

  it('opens Raw files on the files a digest names', async () => {
    const wrapper = setup([digest()])
    const sidebarMenu = useSidebarMenu()
    sidebarMenu.open = true

    await button(wrapper, 'Show files').trigger('click')

    const { app } = mocks
    expect(app.data.instrument.focus).toHaveBeenCalledWith({ instrument: 'Orbi-1' })
    expect(app.data.acquisition.processingStatus).toEqual(['needs_chemistry'])
    // A minute before the oldest file it names, by acquisition time.
    expect(app.data.acquisition.time.range.min.toISOString()).toBe('2026-09-20T07:59:00.000Z')
    expect(app.data.acquisition.time.range.max).toBe(null)
    expect(app.ui.tab.active).toBe('raw files')
    expect(sidebarMenu.open).toBe(false)
    expect(app.ui.inbox.markRead).toHaveBeenCalledWith(['n1'])
  })
})
