import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// The Manage users table's Two-factor column, rendered from the rows the users
// API returns. Under a policy covering editors a machine account comes back
// exempt and a human editor without a factor pending; the column must print
// the one as "exempt" and the other as "pending", or the operator's count of
// accounts still owing an enrolment includes ones that never can.

const rows = [
  {
    id: 2,
    username: 'agent',
    email: 'device-1@agents.example.org',
    role_name: 'editor',
    account_type: 'machine',
    mfa_enabled: false,
    mfa_exempt: true,
    mfa_enrollment_required: false
  },
  {
    id: 3,
    username: 'person',
    email: 'person@example.org',
    role_name: 'editor',
    account_type: 'person',
    mfa_enabled: false,
    mfa_exempt: false,
    mfa_enrollment_required: true
  }
]

vi.mock('@/api', () => ({
  api: { http: { get: vi.fn().mockResolvedValue({ policy_min_role: 'editor' }) } }
}))

vi.mock('@/stores', () => ({
  useApp: () => ({
    auth: { user: { id: 1, role_name: 'owner' } },
    data: { user: { list: rows } }
  })
}))

vi.mock('@/lib/runtime', () => ({ runtime: { meta: {}, config: {}, version: null } }))
vi.mock('@/lib/base', () => ({ BaseCopyableField: { template: '<div />' } }))

vi.mock('primevue/useconfirm', () => ({ useConfirm: () => ({ require: vi.fn() }) }))

import DialogUserManagement from '@/lib/dialogs/DialogUserManagement.vue'

const stub = (tag) => ({ template: `<${tag}><slot /></${tag}>` })

// DataTable hands its rows to the columns, and each column renders its body
// slot once per row - enough of the real contract to read a cell.
const DataTable = {
  props: ['value'],
  provide() {
    return { rows: () => this.value }
  },
  template: '<div><slot /></div>'
}
const Column = {
  props: ['header'],
  inject: ['rows'],
  template:
    '<div :data-column="header"><div v-for="row in rows()" :key="row.id" :data-row="row.username"><slot name="body" :data="row" /></div></div>'
}

// Records each tooltip's text on its element, so a test can read which text a
// binding was given.
const tooltip = {
  mounted(el, binding) {
    el.dataset.tooltip = binding.value
  },
  updated(el, binding) {
    el.dataset.tooltip = binding.value
  }
}

const mountDialog = async () => {
  const wrapper = mount(DialogUserManagement, {
    props: { visible: true },
    global: {
      directives: { tooltip },
      stubs: {
        Dialog: stub('div'),
        DataTable,
        Column,
        Select: stub('div'),
        Button: stub('button'),
        InputText: stub('input'),
        FloatLabel: stub('div'),
        Message: stub('div'),
        DialogMfaReauth: stub('div')
      }
    }
  })
  await flushPromises()
  return wrapper
}

const cell = (wrapper, username) =>
  wrapper.find(`[data-column="Two-factor"] [data-row="${username}"]`).text()

const stateTooltip = (wrapper, username) =>
  wrapper.find(`[data-column="Two-factor"] [data-row="${username}"] .pi`).attributes('data-tooltip')

describe('DialogUserManagement two-factor column', () => {
  it('marks a machine account exempt and a human editor pending', async () => {
    const wrapper = await mountDialog()
    expect(cell(wrapper, 'agent')).toBe('exempt')
    expect(cell(wrapper, 'person')).toBe('pending')
  })

  it("explains each row's state in its tooltip", async () => {
    const wrapper = await mountDialog()
    expect(stateTooltip(wrapper, 'agent')).toBe(
      'Not required: a machine account never signs in interactively'
    )
    expect(stateTooltip(wrapper, 'person')).toBe('Required for this role, not set up yet')
  })
})
