import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// Settings opens the Deleted datasets dialog for every signed-in user, a guest
// included: the server decides which records each one sees, so the button is
// not kept to the Admin section. It sits in a Data section of its own, after
// API Access Tokens and before Admin, with its own help text. The Reference
// lists dialog sits beside it, open to everyone too: the lists are the
// deployment's, not a workspace's.

const mocks = vi.hoisted(() => ({ app: null }))

vi.mock('@/api', () => ({ api: { http: { get: vi.fn(), post: vi.fn() } } }))
vi.mock('@/stores', () => ({ useApp: () => mocks.app }))
vi.mock('@/lib/base', () => ({ BaseCopyableField: true, BaseEditableField: true }))
vi.mock('@/lib/dialogs', () => {
  const dialog = (name) => ({
    name,
    props: ['visible'],
    template: `<div v-if="visible" class="${name}" />`
  })
  return {
    DialogUserManagement: dialog('DialogUserManagement'),
    DialogPasswordChange: dialog('DialogPasswordChange'),
    DialogAgentPairing: dialog('DialogAgentPairing'),
    DialogMfaSetup: dialog('DialogMfaSetup'),
    DialogMfaReauth: dialog('DialogMfaReauth'),
    DialogDeletedDatasets: dialog('DialogDeletedDatasets'),
    DialogReferenceLists: dialog('DialogReferenceLists')
  }
})
vi.mock('@/lib/dialogs/useMfaReauth', async () => {
  const { ref } = await import('vue')
  return {
    useMfaReauth: () => ({
      reauthVisible: ref(false),
      runWithReauth: (fn) => fn(),
      onVerified: () => () => {}
    })
  }
})

import UserSettingsPane from '@/lib/toolbars/ToolbarAppFilters/SidebarMenu/UserSettingsPane.vue'

const ButtonStub = {
  props: ['label', 'icon'],
  emits: ['click'],
  template: `<button :data-icon="icon" @click="$emit('click')">{{ label }}</button>`
}

const mountPane = () =>
  mount(UserSettingsPane, {
    global: {
      stubs: { Button: ButtonStub, ToggleSwitch: true, Message: true, Select: true },
      directives: { helpLayer: {} }
    }
  })

beforeEach(() => {
  setActivePinia(createPinia())
  mocks.app = {
    auth: { user: { id: 1, email: 'guest@example.org', role_id: 100, role_name: 'guest' } },
    data: { user: { update: vi.fn() } },
    ui: {
      darkmode: { active: false },
      notification: { push: vi.fn() },
      help: {
        set: vi.fn(),
        // Keep each section's help text on the element, so a test can read it.
        directive: () => ({
          mounted: (el, { value }) => {
            el.dataset.help = typeof value === 'string' ? value : value.message
          }
        }),
        docUrl: (path = '') => `/docs/${path}`
      }
    }
  }
})

describe('UserSettingsPane', () => {
  it('opens the Deleted datasets dialog from its button, for a guest too', async () => {
    const wrapper = mountPane()
    expect(wrapper.find('.DialogDeletedDatasets').exists()).toBe(false)
    // A guest has no Admin section; the button is outside it.
    expect(wrapper.text()).not.toContain('Manage users')

    const button = wrapper.findAll('button').find((b) => b.text() === 'Deleted datasets')
    expect(button, 'no Deleted datasets button').toBeDefined()
    expect(button.attributes('data-icon')).toContain('ph-clock-counter-clockwise')
    await button.trigger('click')
    expect(wrapper.find('.DialogDeletedDatasets').exists()).toBe(true)
  })

  it('opens the Reference lists dialog from its button, for a guest too', async () => {
    const wrapper = mountPane()
    expect(wrapper.find('.DialogReferenceLists').exists()).toBe(false)

    const button = wrapper.findAll('button').find((b) => b.text() === 'Reference lists')
    expect(button, 'no Reference lists button').toBeDefined()
    expect(button.attributes('data-icon')).toContain('ph-books')
    await button.trigger('click')
    expect(wrapper.find('.DialogReferenceLists').exists()).toBe(true)
    expect(wrapper.find('.DialogDeletedDatasets').exists()).toBe(false)
  })

  it('puts the buttons in a Data section with its own help, not in Account', () => {
    mocks.app.auth.user.role_id = 400
    const wrapper = mountPane()
    const sections = wrapper.findAll('section')
    const titles = sections.map((section) => section.find('h3').text())
    expect(titles).toEqual(['Account', 'Theme', 'API Access Tokens', 'Data', 'Admin'])

    const section = (title) => sections[titles.indexOf(title)]
    const data = section('Data')
    expect(data.findAll('button').map((b) => b.text())).toEqual([
      'Deleted datasets',
      'Reference lists'
    ])
    expect(data.element.dataset.help).toContain('<b>Data</b>')
    expect(data.element.dataset.help).toContain('Deleted datasets lists a record')
    expect(data.element.dataset.help).toContain('Reference lists shows the compound lists')

    const account = section('Account')
    expect(account.text()).not.toContain('Deleted datasets')
    expect(account.element.dataset.help).not.toContain('Deleted datasets')
  })
})
