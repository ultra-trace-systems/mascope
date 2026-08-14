import axios from 'axios'

import { runtime } from '@/lib/runtime.js'

import { useApp } from '@/stores'
import { api } from './client.js'
import handlers from './handlers.js'
import { getApiErrorMessage } from './utils.js'

const API_RESPONSE_TIMEOUT = 20_000 // 20 seconds

// Per-request override for operations that legitimately take long on large
// inputs (e.g. creating a target collection computes ion/isotope patterns for
// every new compound). Pass as `timeout` in the request config.
export const API_SLOW_RESPONSE_TIMEOUT = 300_000 // 5 minutes

export const initHttp = () => {
  const client = axios.create({
    baseURL: `${runtime.api_path}/api`,
    withCredentials: true,
    timeout: API_RESPONSE_TIMEOUT
  })
  client.interceptors.request.use(handleRequestData, handleClientError)
  client.interceptors.response.use(handleResponseData, handleServerError)
  return client
}

// DATA HANDLING

function handleRequestData(config) {
  const { method, url } = config
  console.debug(`▶️ [api:http] request ${method} ${url}`, config)
  // session id
  const sid = api?.socket?.id
  if (sid) {
    config.headers['X-SID'] = sid
  } else {
    console.warn(`▶️ [api:http] socket SID not available for request ${method} ${url}`, {
      hasApi: !!api,
      hasSocket: !!api?.socket,
      socketConnected: api?.socket?.connected,
      socketId: api?.socket?.id
    })
  }
  // handler
  let use, type
  ;({ use, type, ...config } = config)
  if (use) {
    config.headers['X-Handler'] = use
  }
  if (type) {
    config.headers['X-Type'] = type
  }
  return config
}

function handleResponseData(response) {
  const { method, url } = response?.config || {}
  console.debug(`✅ [api:http] response ${method} ${url}`, response)
  // pick the handler
  const handlerName = response?.config?.headers['X-Handler']
  const handler = handlerName ? handlers[handlerName] : (resp) => resp
  // if a handler is defined
  return handler(response)
}

// ERROR HANDLING

function handleClientError(error) {
  // Request-interceptor failures may have no config/response at all, so guard
  // both before destructuring (otherwise the real error is masked by a
  // TypeError thrown here).
  const { method, url, headers } = error?.config || {}
  const type = headers?.['X-Type']
  // log to console for developers
  const { detail } = error?.response?.data || {}
  const message = getApiErrorMessage(error)
  console.error(
    `❌[api:http] ${method} ${url} client failure: ${message} (error_id: ${detail?.error_id})`,
    error
  )
  // emit notification to users
  const app = useApp()
  app.ui.notification.push({
    type,
    status: 'error',
    message
  })
  // throw
  return Promise.reject(error)
}

/**
 * Handles unauthorized access (401 responses)
 * Triggers auth check which will show login page if token is expired or cookie is removed
 */
function handleUnauthorizedError(error) {
  const app = useApp()
  app.ui.notification.push({
    type: 'user_session_expired',
    status: 'warning',
    message: 'Your session has expired. Please log in again.'
  })

  app.auth.identify()
  return Promise.reject(error)
}

/**
 * Handles server-side errors from API responses.
 * Part of axios interceptor chain, runs after handleResponseData and specific handlers.
 * One of purposes is catching unhandled 401s to trigger auth check.
 */
function handleServerError(error) {
  const { method, url, headers } = error?.config || {}
  const type = headers?.['X-Type'] ?? 'unknown'

  // The account owes a password change. Matched on the code rather than the
  // status: this is a 403 so the client is not sent back to the sign-in screen
  // (the session is fine, the action is not), and prose is not a contract.
  // Reachable only mid-session or in a stale tab - at sign-in the auth store
  // learns the flag from /users/me and never issues these requests.
  if (error?.response?.data?.detail?.code === 'password_change_required') {
    const app = useApp()
    if (app.auth.requirePasswordChange()) {
      app.ui.notification.push({
        type,
        status: 'warning',
        message: 'Your account needs a new password before you can continue.'
      })
    }
    return Promise.reject(error)
  }

  // Any unhandled 401 triggers auth check
  if (error?.response?.status === 401) {
    return handleUnauthorizedError(error)
  }

  // Handle timeout errors (no response from server)
  if (error.code === 'ECONNABORTED' || !error.response) {
    console.error(`⏱️ [api:http] ${type} ${method} ${url} timeout or network error`, error)
    const app = useApp()
    app.ui.notification.push({
      type,
      status: 'error',
      message: 'Request timed out. Please try again or contact support.'
    })
    return Promise.reject(error)
  }

  // log to console for developers
  const { detail } = error?.response?.data || {}
  const message = getApiErrorMessage(error)
  console.error(
    `🚫 [api:http] ${type} ${method} ${url} server failure: ${message} (error_id: ${detail?.error_id})`,
    error
  )
  // emit notification to users
  const app = useApp()
  app.ui.notification.push({
    type,
    status: 'error',
    message
  })
  // throw
  return Promise.reject(error)
}
