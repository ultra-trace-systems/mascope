import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { Decoder } from 'socket.io-parser'

import { initSocket } from '@/api/socket'

// A payload failing to decode client-side (invalid JSON, attachment overflow)
// makes socket.io close the connection with reason "parse error". These tests
// pin down the two defenses: the decoder must not cap binary attachments for
// our own backend, and a decode failure must not trigger the reconnect page
// reload (which historically looped: reload -> auto-fired request -> same bad
// payload -> parse error -> reload).

const { pushSpy, ioState } = vi.hoisted(() => ({
  pushSpy: vi.fn(),
  ioState: { lastOpts: null, socket: null }
}))

vi.mock('socket.io-client', () => ({
  io: (url, opts) => {
    ioState.lastOpts = opts
    return ioState.socket
  }
}))
vi.mock('@/stores', () => ({
  useApp: () => ({ ui: { notification: { push: pushSpy } } })
}))
vi.mock('@/lib/runtime.js', () => ({
  runtime: { mode: 'prod', meta: {} }
}))

function makeFakeSocket() {
  const handlers = new Map()
  // The manager (`socket.io`) emits the reconnection events on its own bus.
  const managerHandlers = new Map()
  const add = (event, fn) => {
    if (!handlers.has(event)) handlers.set(event, [])
    handlers.get(event).push(fn)
  }
  const addManager = (event, fn) => {
    if (!managerHandlers.has(event)) managerHandlers.set(event, [])
    managerHandlers.get(event).push(fn)
  }
  return {
    connected: true,
    on: add,
    once: add,
    onAny: () => {},
    emit: vi.fn(),
    io: { on: addManager },
    fire(event, ...args) {
      for (const fn of handlers.get(event) ?? []) fn(...args)
    },
    fireManager(event, ...args) {
      for (const fn of managerHandlers.get(event) ?? []) fn(...args)
    }
  }
}

describe('initSocket', () => {
  let socket
  let reloadSpy

  beforeEach(async () => {
    vi.clearAllMocks()
    ioState.socket = makeFakeSocket()
    reloadSpy = vi.spyOn(window.location, 'reload').mockImplementation(() => {})
    ;({ socket } = await initSocket())
  })

  afterEach(() => {
    reloadSpy.mockRestore()
  })

  it('configures a decoder without a practical attachment limit', () => {
    // 500 attachments: a binary event header a large ion focus payload produces
    const bigBinaryHeader = '5500-["visualization_signal_sum_spectrum"]'

    const CustomDecoder = ioState.lastOpts.parser.Decoder
    expect(() => new CustomDecoder().add(bigBinaryHeader)).not.toThrow()

    // the stock decoder rejects it, which is why the override exists
    expect(() => new Decoder().add(bigBinaryHeader)).toThrow(/too many attachments/)
  })

  it('reloads the page on reconnect after a network-level drop', () => {
    socket.fire('disconnect', 'transport close')
    socket.fire('connect')

    expect(reloadSpy).toHaveBeenCalledTimes(1)
  })

  it('skips the reload when the disconnect was a payload decode failure', () => {
    socket.addSubscription('user-1')
    socket.fire('disconnect', 'parse error')
    socket.fire('connect')

    expect(reloadSpy).not.toHaveBeenCalled()
    // the socket still recovers: rooms re-subscribed, reconnect reported
    expect(socket.emit).toHaveBeenCalledWith('subscribe', 'user-1')
    expect(pushSpy).toHaveBeenCalledWith(expect.objectContaining({ status: 'success' }))
  })

  it('reloads again on a later reconnect for a different reason', () => {
    socket.fire('disconnect', 'parse error')
    socket.fire('connect')
    socket.fire('disconnect', 'transport close')
    socket.fire('connect')

    expect(reloadSpy).toHaveBeenCalledTimes(1)
  })

  const reconnectNotices = () =>
    pushSpy.mock.calls.filter(([n]) => n.type === 'connection' && n.status === 'info')

  it('reports a disconnection once, not once per retry', () => {
    // Socket.IO retries forever with backoff (every 5 s at the default cap), so
    // a notification per attempt fills the log's 250-entry retention in about
    // twenty minutes offline and evicts every real notification.
    socket.fire('disconnect', 'transport close')
    socket.fireManager('reconnect_attempt', 1)
    socket.fireManager('reconnect_attempt', 2)
    socket.fireManager('reconnect_attempt', 3)

    expect(reconnectNotices()).toHaveLength(1)
  })

  it('reports the next disconnection again', () => {
    socket.fire('disconnect', 'transport close')
    socket.fireManager('reconnect_attempt', 1)
    socket.fireManager('reconnect_attempt', 2)
    socket.fire('connect')
    socket.fire('disconnect', 'ping timeout')
    socket.fireManager('reconnect_attempt', 1)

    expect(reconnectNotices()).toHaveLength(2)
  })
})
