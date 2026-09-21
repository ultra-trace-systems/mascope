import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// Kept notifications live on the server until read. The store loads them when
// a person signs in, follows the digests the server changes over the socket,
// and marks them read.

const handlers = {}
const loginCallbacks = []

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
  useAuth: () => ({ onLogin: (callback) => loginCallbacks.push(callback) })
}))

let api
let useInbox

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.resetModules()
  loginCallbacks.length = 0
  for (const event of Object.keys(handlers)) delete handlers[event]
  ;({ api } = await import('@/api'))
  api.http.post.mockResolvedValue({ data: { data: { read: 1 } } })
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
  ...overrides
})

describe('inbox store', () => {
  it("loads a person's notifications, read ones too, when they sign in", async () => {
    api.http.get.mockResolvedValue({ data: { data: [digest('n1')] } })
    const inbox = useInbox()

    expect(loginCallbacks).toHaveLength(1)
    await loginCallbacks[0]()

    const [url, config] = api.http.get.mock.calls[0]
    expect(url).toBe('/notifications')
    expect(config.params.include_read).toBe(true)
    expect(inbox.items.map(({ notification_id }) => notification_id)).toEqual(['n1'])
  })

  it('keeps what it has when the server cannot list them', async () => {
    api.http.get.mockRejectedValue(new Error('404'))
    const inbox = useInbox()

    await expect(inbox.load()).resolves.toBeUndefined()
    expect(inbox.items).toEqual([])
  })

  it('adds a digest the server opens and replaces one it changes', () => {
    const inbox = useInbox()

    handlers.notification_created({ record: digest('n1') })
    handlers.notification_created({ record: digest('n2') })
    handlers.notification_updated({ record: digest('n1', { count: 3 }) })

    expect(inbox.items).toHaveLength(2)
    expect(inbox.items.find(({ notification_id }) => notification_id === 'n1').count).toBe(3)
  })

  it('lists unread digests first, then the most recently changed', () => {
    const inbox = useInbox()
    inbox.items = [
      digest('old-unread', { updated_utc: '2026-09-20T10:00:00+00:00' }),
      digest('new-read', {
        updated_utc: '2026-09-21T11:00:00+00:00',
        read_utc: '2026-09-21T11:30:00+00:00'
      }),
      digest('new-unread', { updated_utc: '2026-09-21T10:00:00+00:00' })
    ]

    expect(inbox.sorted.map(({ notification_id }) => notification_id)).toEqual([
      'new-unread',
      'old-unread',
      'new-read'
    ])
  })

  it('counts the unread digests and the errors among them', () => {
    const inbox = useInbox()
    inbox.items = [
      digest('e'),
      digest('w', { severity: 'warning', kind: 'needs_chemistry' }),
      digest('read', { read_utc: '2026-09-21T11:30:00+00:00' })
    ]

    expect(inbox.unread).toHaveLength(2)
    expect(inbox.unreadErrors).toBe(1)
  })

  it('marks the digests it is given read, on the server and here', async () => {
    const inbox = useInbox()
    inbox.items = [digest('n1'), digest('n2')]

    await inbox.markRead(['n1'])

    expect(api.http.post).toHaveBeenCalledWith(
      '/notifications/read',
      { notification_ids: ['n1'] },
      expect.anything()
    )
    expect(inbox.unread.map(({ notification_id }) => notification_id)).toEqual(['n2'])
  })

  it('marks every unread digest read at once', async () => {
    const inbox = useInbox()
    inbox.items = [digest('n1'), digest('n2')]

    await inbox.markAllRead()

    expect(api.http.post).toHaveBeenCalledWith(
      '/notifications/read',
      { all: true },
      expect.anything()
    )
    expect(inbox.unread).toHaveLength(0)
  })

  it('asks the server nothing when there is nothing to mark', async () => {
    const inbox = useInbox()

    await inbox.markRead([])
    await inbox.markAllRead()

    expect(api.http.post).not.toHaveBeenCalled()
  })
})
