import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// The members dialog has to hear about memberships it did not make itself:
// another administrator editing the same workspace, or an account being
// registered, which enrols the new account in every system workspace. Both go
// through the member controller, which broadcasts `workspace_reload` naming
// the workspace - so the dialog listens while it is open, and only acts on the
// broadcasts about the workspace it is showing.

// Socket handlers are captured as the dialog registers them, so a test can
// fire the broadcast at exactly the handler the app would receive it on, and
// watch the registration go away again on teardown.
const handlers = new Set()
const rooms = new Set()

vi.mock('@/api', () => ({
  api: {
    socket: {
      on: (event, handler) => {
        if (event === 'workspace_reload') handlers.add(handler)
      },
      off: (event, handler) => {
        if (event === 'workspace_reload') handlers.delete(handler)
      },
      addSubscription: (room) => rooms.add(room),
      removeSubscription: (room) => rooms.delete(room)
    }
  }
}))

const getMembers = vi.fn()
// The workspace the sidebar has selected. The store owns that room's
// subscription, so the dialog must leave it alone; anything else it has to
// join itself, or the broadcast never reaches this tab.
let focusedId = 'ws-focused'

vi.mock('@/stores', () => ({
  useApp: () => ({
    auth: { user: { id: 1, role_name: 'owner' } },
    data: {
      user: { list: [] },
      workspace: {
        getMembers,
        get focusedId() {
          return focusedId
        }
      }
    }
  })
}))

vi.mock('primevue/useconfirm', () => ({ useConfirm: () => ({ require: vi.fn() }) }))

import DialogWorkspaceMembership from '@/lib/dialogs/DialogWorkspaceMembership.vue'

const WORKSPACE = { workspace_id: 'ws-on-screen', workspace_name: 'Acquisitions' }

const stub = (tag) => ({ template: `<${tag}><slot /></${tag}>` })

const mountDialog = () =>
  mount(DialogWorkspaceMembership, {
    props: { visible: false, workspace: WORKSPACE },
    global: {
      directives: { tooltip: {} },
      stubs: {
        Dialog: stub('div'),
        DataTable: stub('div'),
        Column: stub('div'),
        Select: stub('div'),
        Button: stub('button')
      }
    }
  })

/** Open the dialog and settle the roster load that opening triggers. */
const openDialog = async (wrapper) => {
  await wrapper.setProps({ visible: true })
  await flushPromises()
  expect(getMembers).toHaveBeenCalledWith(WORKSPACE.workspace_id)
  getMembers.mockClear()
}

/** Deliver a member-controller reload broadcast to whoever is listening. */
const announce = async (record_id) => {
  for (const handler of handlers) {
    handler({
      event_id: 'evt-1',
      timestamp: '2026-08-26T00:00:00Z',
      operation: 'reload',
      record_type: 'workspace',
      record_id
    })
  }
  await flushPromises()
}

beforeEach(() => {
  handlers.clear()
  rooms.clear()
  focusedId = 'ws-focused'
  getMembers.mockReset()
  getMembers.mockResolvedValue({
    data: [{ user_id: 1, username: 'chemist', workspace_role: 'owner' }]
  })
})

describe('DialogWorkspaceMembership live roster refresh', () => {
  it('reloads when its own workspace announces a membership change', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)

    await announce(WORKSPACE.workspace_id)

    expect(getMembers).toHaveBeenCalledWith(WORKSPACE.workspace_id)
  })

  it('ignores an announcement about another workspace', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)

    await announce('ws-elsewhere')

    expect(getMembers).not.toHaveBeenCalled()
  })

  it('stops listening once the dialog is closed', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    expect(handlers.size).toBe(1)

    await wrapper.setProps({ visible: false })
    await flushPromises()

    expect(handlers.size).toBe(0)
  })

  it('stops listening when the dialog is torn down still open', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)
    expect(handlers.size).toBe(1)

    wrapper.unmount()

    expect(handlers.size).toBe(0)
    expect(rooms.size).toBe(0)
  })

  // The workspace pane's context menu opens this dialog on the right-clicked
  // workspace, which need not be the one the sidebar has selected. The
  // broadcast only reaches rooms this tab joined, so without joining, the
  // roster on that path would go stale exactly as it did before.
  it('joins the room of a workspace the sidebar has not selected', async () => {
    const wrapper = mountDialog()
    await openDialog(wrapper)

    expect(rooms.has(WORKSPACE.workspace_id)).toBe(true)

    await announce(WORKSPACE.workspace_id)
    expect(getMembers).toHaveBeenCalledWith(WORKSPACE.workspace_id)

    await wrapper.setProps({ visible: false })
    await flushPromises()
    expect(rooms.has(WORKSPACE.workspace_id)).toBe(false)
  })

  // Unsubscribing from the focused workspace would take the workspace store's
  // own live updates down with it, so that one room is left alone entirely.
  it('leaves the focused workspace subscription to the store', async () => {
    focusedId = WORKSPACE.workspace_id
    const wrapper = mountDialog()
    await openDialog(wrapper)

    expect(rooms.size).toBe(0)

    await wrapper.setProps({ visible: false })
    await flushPromises()

    expect(rooms.size).toBe(0)
  })
})
