import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick, reactive } from 'vue'

// Kept notifications live on the server until read. The store loads the
// unread ones when a person signs in and whenever the pane opens, follows the
// digests the server changes over the socket, and marks them read as they
// were shown.

const handlers = {}
const loginCallbacks = []
const auth = reactive({ user: { id: 7 } })

vi.mock('@/api', () => ({
  api: {
    http: { get: vi.fn(), post: vi.fn() },
    socket: {
      on: vi.fn((event, handler) => {
        handlers[event] = handler
      })
    }
  }
}))

vi.mock('@/stores/auth', () => ({
  useAuth: () => {
    auth.onLogin = (callback) => loginCallbacks.push(callback)
    return auth
  }
}))

let api
let useInbox

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  loginCallbacks.length = 0
  auth.user = { id: 7 }
  for (const event of Object.keys(handlers)) delete handlers[event]
  ;({ api } = await import('@/api'))
  api.http.post.mockImplementation(async (_url, body) => ({
    data: {
      data: {
        read: body.notifications.length,
        notification_ids: body.notifications.map(({ notification_id }) => notification_id)
      }
    }
  }))
  ;({ useInbox } = await import('@/stores/ui/inbox'))
})

const digest = (id, overrides = {}) => ({
  notification_id: id,
  user_id: 7,
  kind: 'processing_failed',
  severity: 'error',
  instrument: 'Orbi-1',
  message: 'Processing failed for 1 file from Orbi-1.',
  count: 1,
  payload: { status: 'failed', files: [] },
  updated_utc: '2026-09-21T10:00:00+00:00',
  read_utc: null,
  resolved_utc: null,
  version: 1,
  ...overrides
})

const ids = (rows) => rows.map(({ notification_id }) => notification_id)

// A load whose answer the test hands over when it chooses.
const pendingLoad = () => {
  let answer
  api.http.get.mockImplementationOnce(
    () => new Promise((resolve) => (answer = (rows) => resolve({ data: { data: rows } })))
  )
  return (rows) => answer(rows)
}

describe('inbox store: loading', () => {
  it("loads a person's unread notifications when they sign in", async () => {
    api.http.get.mockResolvedValue({ data: { data: [digest('n1')] } })
    const inbox = useInbox()

    expect(loginCallbacks).toHaveLength(1)
    await loginCallbacks[0]()

    const [url, config] = api.http.get.mock.calls[0]
    expect(url).toBe('/notifications')
    expect(config.params.include_read).toBeUndefined()
    expect(ids(inbox.items)).toEqual(['n1'])
  })

  it('keeps what it has when the server cannot list them', async () => {
    const inbox = useInbox()
    inbox.items = [digest('n1')]
    api.http.get.mockRejectedValue(new Error('502'))

    await expect(inbox.load()).resolves.toBeUndefined()
    expect(ids(inbox.items)).toEqual(['n1'])
  })

  it('starts from nothing when another account signs in', async () => {
    const inbox = useInbox()
    inbox.items = [digest('theirs')]

    auth.user = false
    await nextTick()

    expect(inbox.items).toEqual([])
  })

  it('drops an answer meant for the account signed in before', async () => {
    const inbox = useInbox()
    const answer = pendingLoad()

    const loading = inbox.load()
    auth.user = { id: 8 }
    await nextTick()
    answer([digest('for-7')])
    await loading

    expect(inbox.items).toEqual([])
  })

  it("keeps an update of the account before out of the next one's load", async () => {
    const inbox = useInbox()
    const answerFor7 = pendingLoad()
    const loadingFor7 = inbox.load()
    handlers.notification_updated({ record: digest('for-7', { version: 2 }) })

    auth.user = { id: 8 }
    await nextTick()
    const answerFor8 = pendingLoad()
    const loadingFor8 = inbox.load()
    answerFor7([digest('for-7')])
    await loadingFor7
    answerFor8([digest('for-8', { user_id: 8 })])
    await loadingFor8

    expect(ids(inbox.items)).toEqual(['for-8'])
  })

  it('applies an update that arrived during a load on top of its answer', async () => {
    const inbox = useInbox()
    const answer = pendingLoad()

    const loading = inbox.load()
    handlers.notification_updated({ record: digest('n1', { count: 2, version: 2 }) })
    answer([digest('n1')])
    await loading

    expect(inbox.items[0].count).toBe(2)
  })
})

describe('inbox store: updates', () => {
  it('adds a digest the server opens and replaces one it changes', () => {
    const inbox = useInbox()

    handlers.notification_created({ record: digest('n1') })
    handlers.notification_created({ record: digest('n2') })
    handlers.notification_updated({ record: digest('n1', { count: 3, version: 2 }) })

    expect(inbox.items).toHaveLength(2)
    expect(inbox.items.find(({ notification_id }) => notification_id === 'n1').count).toBe(3)
  })

  it('leaves out a row addressed to another account', () => {
    const inbox = useInbox()

    handlers.notification_created({ record: digest('theirs', { user_id: 8 }) })

    expect(inbox.items).toEqual([])
  })

  it('keeps the newer copy of a row whichever arrives last', () => {
    const inbox = useInbox()

    handlers.notification_updated({ record: digest('n1', { count: 3, version: 3 }) })
    handlers.notification_updated({ record: digest('n1', { count: 2, version: 2 }) })

    expect(inbox.items[0].count).toBe(3)
  })

  it('lets a digest read elsewhere go', () => {
    const inbox = useInbox()
    inbox.items = [digest('n1')]

    handlers.notification_updated({
      record: digest('n1', { read_utc: '2026-09-21T11:00:00+00:00', version: 2 })
    })

    expect(inbox.items).toEqual([])
  })

  it('lists the most recently changed first', () => {
    const inbox = useInbox()
    inbox.items = [
      digest('old', { updated_utc: '2026-09-20T10:00:00+00:00' }),
      digest('new', { updated_utc: '2026-09-21T10:00:00+00:00' })
    ]

    expect(ids(inbox.sorted)).toEqual(['new', 'old'])
  })

  it('counts what is pending: unread and not resolved, errors apart', () => {
    const inbox = useInbox()
    inbox.items = [
      digest('e'),
      digest('w', { severity: 'warning', kind: 'needs_chemistry' }),
      digest('settled', { resolved_utc: '2026-09-21T11:30:00+00:00' })
    ]

    expect(inbox.unread).toHaveLength(3)
    expect(ids(inbox.pending)).toEqual(['e', 'w'])
    expect(inbox.pendingErrors).toBe(1)
  })
})

describe('inbox store: marking read', () => {
  it('marks digests read as they were shown', async () => {
    const inbox = useInbox()
    inbox.items = [digest('n1'), digest('n2')]

    await inbox.markRead(['n1'])

    expect(api.http.post).toHaveBeenCalledWith(
      '/notifications/read',
      { notifications: [{ notification_id: 'n1', updated_utc: '2026-09-21T10:00:00+00:00' }] },
      expect.anything()
    )
    expect(ids(inbox.items)).toEqual(['n2'])
  })

  it('keeps a digest the server left unread: it took a file since', async () => {
    const inbox = useInbox()
    inbox.items = [digest('n1'), digest('n2')]
    api.http.post.mockResolvedValue({ data: { data: { read: 1, notification_ids: ['n2'] } } })

    await inbox.markAllRead()

    expect(
      api.http.post.mock.calls[0][1].notifications.map(({ notification_id }) => notification_id)
    ).toEqual(['n1', 'n2'])
    expect(ids(inbox.items)).toEqual(['n1'])
  })

  it('asks the server nothing when there is nothing to mark', async () => {
    const inbox = useInbox()

    await inbox.markRead([])
    await inbox.markAllRead()

    expect(api.http.post).not.toHaveBeenCalled()
  })
})
