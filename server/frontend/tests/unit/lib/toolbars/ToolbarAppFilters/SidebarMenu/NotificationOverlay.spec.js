import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'

// The badge on the home button counts what there is to read: the live errors
// and warnings since the pane was last opened, and the kept notifications not
// yet marked read. Errors win the count and the colour.

const mocks = vi.hoisted(() => ({ app: null }))
vi.mock('@/stores', () => ({ useApp: () => mocks.app }))

import NotificationOverlay from '@/lib/toolbars/ToolbarAppFilters/SidebarMenu/NotificationOverlay.vue'

const OverlayBadge = {
  props: ['value', 'severity'],
  template: '<div class="badge" :data-value="value" :data-severity="severity"><slot /></div>'
}

const mountBadge = ({ recentErrors = 0, recentWarnings = 0, unread = [], unreadErrors = 0 }) => {
  mocks.app = reactive({
    ui: {
      notification: { recentErrors, recentWarnings },
      inbox: { unread, unreadErrors }
    }
  })
  return mount(NotificationOverlay, {
    slots: { default: '<button>home</button>' },
    global: { stubs: { OverlayBadge } }
  })
}

const kept = (severity) => ({ notification_id: severity, severity })

describe('NotificationOverlay', () => {
  it('is hidden when there is nothing to read', () => {
    const wrapper = mountBadge({})

    expect(wrapper.find('.badge').exists()).toBe(false)
    expect(wrapper.text()).toBe('home')
  })

  it('counts a kept notification that is not read yet', () => {
    const wrapper = mountBadge({ unread: [kept('warning')] })

    const badge = wrapper.get('.badge')
    expect(badge.attributes('data-value')).toBe('1')
    expect(badge.attributes('data-severity')).toBe('warn')
  })

  it('adds the kept errors to the live ones', () => {
    const wrapper = mountBadge({
      recentErrors: 2,
      recentWarnings: 5,
      unread: [kept('error'), kept('warning')],
      unreadErrors: 1
    })

    const badge = wrapper.get('.badge')
    expect(badge.attributes('data-value')).toBe('3')
    expect(badge.attributes('data-severity')).toBe('danger')
  })

  it('counts warnings when there are no errors', () => {
    const wrapper = mountBadge({ recentWarnings: 2, unread: [kept('warning')] })

    expect(wrapper.get('.badge').attributes('data-value')).toBe('3')
  })
})
