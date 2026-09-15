import { io } from 'socket.io-client'
import { Encoder, Decoder } from 'socket.io-parser'

import { useApp } from '@/stores'
import { runtime } from '@/lib/runtime.js'
import { ref } from 'vue'

// Custom parser to lift the decoder's attachment limit (default 10 in
// socket.io-parser 4.2.6+, a guard against untrusted servers). Visualization
// events send two binary numpy arrays (x + y) per trace and an ion's isotope
// count is unbounded, so any fixed cap is a cliff where the decoder throws
// "too many attachments" and the whole socket dies with a parse error
// disconnect. This client only ever connects to Mascope's own backend, so the
// untrusted-server guard buys nothing here.
const parser = {
  Encoder,
  Decoder: class extends Decoder {
    constructor() {
      super({ maxAttachments: Infinity })
    }
  }
}

const host = location.hostname

export async function initSocket() {
  // init socket in `/` namespace. In prod the socket is proxied by nginx on the
  // same origin, so use the page's actual origin (http or https) rather than
  // assuming HTTPS -- socket.io upgrades it to ws/wss accordingly.
  const url = runtime.mode === 'prod' ? location.origin : `ws://${host}:${runtime.meta.api_port}`
  const socket = io(url, {
    withCredentials: true, // Enables cookie sending
    transports: ['websocket'],
    parser
  })
  const activeSubscriptions = new Set()
  const socketConnected = ref(false)

  console.debug('📭 [api:sio] initialized socket for', runtime.mode, ':', url, socket)

  // Wait for connection
  if (!socket.connected) {
    console.debug('⏳ [api:sio] Waiting for connection...')
    await new Promise((resolve) => socket.once('connect', resolve))
    socketConnected.value = true
    console.debug('✅ [api:sio] Socket connected')
  }
  // logging handlers
  socket.onAny((eventName, ...event) => {
    console.debug(`📬 [api:sio] ${eventName} received:`, event)
  })
  // connection status handlers
  let lastDisconnectReason = null
  // Socket.IO retries forever with backoff (reconnectionAttempts defaults to
  // Infinity, reconnectionDelayMax to 5 s), so notifying per attempt fills the
  // log's 250-entry retention in about twenty minutes offline and evicts every
  // real notification. Report the disconnection, not the retries.
  let reconnectReported = false
  socket.on('disconnect', (reason) => {
    console.warn('⚠️ [api:sio] Socket disconnected:', reason)
    lastDisconnectReason = reason
    reconnectReported = false
    socketConnected.value = false
  })
  socket.io.on('reconnect_attempt', (attempt) => {
    console.debug('🔄 [api:sio] Socket reconnect attempt', attempt)
    if (reconnectReported) return
    reconnectReported = true
    const app = useApp()
    app.ui.notification.push({
      type: 'connection',
      status: 'info',
      message: 'Trying to reconnect...'
    })
  })
  socket.on('connect', () => {
    // Use 'connect' event to detect reconnections, since the socket.io server
    // instance may be different in which case 'reconnect' event won't fire
    console.debug('✅ [api:sio] Socket reconnected')
    // Re-subscribe to all active rooms
    activeSubscriptions.forEach((room) => {
      socket.emit('subscribe', room)
      console.debug(`📬 [api:sio] Re-subscribed to room: ${room}`)
    })
    const app = useApp()
    app.ui.notification.push({
      type: 'connection',
      status: 'success',
      message: 'Reconnected to server'
    })
    socketConnected.value = true
    if (lastDisconnectReason === 'parse error') {
      // A server payload failed to decode on this client and killed the socket.
      // Reloading cannot repair the lost packet and used to loop: reload ->
      // auto-fired request -> same bad payload -> parse error -> reload. Stay
      // up instead; subscriptions were already restored above.
      console.error('🐞 [api:sio] Reconnected after a payload decode failure, skipping page reload')
      return
    }
    // Reload to refetch state that went stale while disconnected (missed
    // socket events); the URL mirror restores the current view on load.
    window.location.reload()
  })

  // Attach subscription management methods to socket
  socket.addSubscription = function (room) {
    activeSubscriptions.add(room)
    this.emit('subscribe', room)
  }
  socket.removeSubscription = function (room) {
    activeSubscriptions.delete(room)
    this.emit('unsubscribe', room)
  }

  return { socket, socketConnected }
}
