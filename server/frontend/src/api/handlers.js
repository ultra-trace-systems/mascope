import { useApp } from '@/stores'

export default {
  /**
   * Basic CRUD Operations
   */
  create: (response) => {
    const { type, status, message, data } = unpack(response)
    const app = useApp()
    if (status == 201) {
      // notify users
      app.ui.notification.push({
        type,
        message,
        status: 'success'
      })
      return data
    } else {
      unhandled(response)
      return
    }
  },
  read: (response) => {
    const { status, data } = unpack(response)
    if (status == 200 || status == 202) {
      return data.data
    } else {
      unhandled(response)
      return
    }
  },
  update: (response) => {
    const { type, status, message } = unpack(response)
    const app = useApp()
    if (status == 200) {
      // notify users
      app.ui.notification.push({
        type,
        message,
        status: 'success'
      })
      return null
    } else {
      unhandled(response)
      return
    }
  },
  delete: (response) => {
    const { type, status, message } = unpack(response)
    const app = useApp()
    if (status === 200) {
      // notify users
      app.ui.notification.push({
        type,
        message,
        status: 'success'
      })
      return null
    } else if (status === 207) {
      // warning for partial deletion
      app.ui.notification.push({
        type,
        message: response?.data?.error || message || 'Some items were not deleted.',
        status: 'warning'
      })
      return null
    } else {
      unhandled(response)
      return
    }
  },
  /**
   * Start Background Process
   *   use with GET, POST
   */
  process: (response) => {
    const { status, data } = unpack(response)
    if (status === 202) {
      console.debug('✅ [api:http] progress notification', data)
      // data is returned in sio user_notifications
    } else {
      unhandled(response)
    }
  },
  /**
   * Authentication
   */
  auth: (response) => {
    const { type, status, data } = unpack(response)
    const app = useApp()

    // Handle owner registration check
    if (type === 'first_owner_status') {
      return data
    }

    // Handle identify_user responses
    if (type === 'identify_user') {
      switch (status) {
        case 200:
          return data.data
        case 401:
          return null
        default:
          // Anything else is treated as "not signed in", which puts the app
          // back on the sign-in screen - where signing in leads straight back
          // here. Notify, so that loop is diagnosable rather than silent.
          unhandled(response)
          app.ui.notification.push({
            type: 'identify_user',
            message: data?.error || 'Could not confirm who you are signed in as.',
            status: 'error'
          })
          return null
      }
    }

    // Handle unauthorized access for other auth types
    if (status === 401) {
      app.ui.notification.push({
        type: 'user_signed_out',
        message: data?.error || 'Please sign in to the Mascope.',
        status: 'warning'
      })
      return null
    }

    // Handle successful responses for other auth types
    if (status === 200 || status === 204) {
      const message = {
        user_sign_in: 'Signed in successfully',
        user_sign_out: 'Signed out successfully',
        user_session_expired:
          'Your login session expired, so you have been signed out. Please sign in again.'
      }
      const knownEvent = type in message
      app.ui.notification.push({
        type,
        message: knownEvent ? message[type] : 'Authentication successful',
        status: 'info'
      })
      if (!knownEvent) {
        console.warn(`⚠️ [api:http] unknown succesful auth event type ${type}`, response)
      }
      return data.data
    }

    // Handle unexpected cases
    unhandled(response)
    return data.data
  }
}

function unpack(response) {
  const { status, data, request, config } = response
  const { method, url } = request
  const message = data?.data?.message ?? data?.message
  const type = config?.headers['X-Type']
  return { type, status, message, data, request, method, url }
}

function unhandled(response) {
  const { status, method, url } = unpack(response)
  console.warn(`⚠️ [api:http] ${method} ${url} response status ${status} unhandled:`, response)
}
