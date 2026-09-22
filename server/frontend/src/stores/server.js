import { ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { useAuth } from '@/stores/auth'

/**
 * A server announcing this keeps a file whose name carries no ionization mode
 * token, for someone to choose its chemistry; an older one fails such a file.
 */
export const TOKENLESS_UPLOADS = 'files_uploads_without_ionization_token'

/**
 * The server this tab talks to: its build, and what it announces it can do.
 *
 * A capability (the backend's `mascope_backend/capabilities.py`) is a
 * behaviour a client may rely on only once the server announces it: this tab
 * may be talking to a server of another build, and an older server announces
 * nothing, which reads as "not supported". Read from `GET /version` at
 * sign-in, and again whenever the About tab is shown.
 *
 * A server that could not be read is not one that lacks a capability: until
 * it answers, `capabilities` stays null, every check says no, and each check
 * asks the server again.
 */
export const useServer = defineStore('app.server', () => {
  const version = ref(null)
  const capabilities = ref(null)
  let loading = null

  function load() {
    loading ??= (async () => {
      version.value = null
      try {
        const data = await api.http.get('/version', {
          use: 'read',
          type: 'version',
          errors: 'inline'
        })
        if (data) {
          version.value = data.version ?? null
          capabilities.value = data.capabilities ?? {}
        }
      } catch {
        // Left as it was; the next check asks again.
      } finally {
        loading = null
      }
    })()
    return loading
  }

  /** Whether the server announces the capability `name`. */
  function can(name) {
    if (capabilities.value === null) {
      load()
      return false
    }
    return capabilities.value[name] === true
  }

  useAuth().onLogin(load)

  return { version, capabilities, can, load }
})
