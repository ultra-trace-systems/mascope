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
 * What the server this tab talks to announces it can do.
 *
 * A capability (the backend's `mascope_backend/capabilities.py`) is a
 * behaviour a client may rely on only once the server announces it: this tab
 * may be talking to a server of another build, and an older server announces
 * nothing, which reads as "not supported". Read at sign-in from `GET /version`.
 */
export const useServer = defineStore('app.server', () => {
  const capabilities = ref({})

  async function load() {
    try {
      const data = await api.http.get('/version', {
        use: 'read',
        type: 'version',
        errors: 'inline'
      })
      capabilities.value = data?.capabilities ?? {}
    } catch {
      capabilities.value = {}
    }
  }

  /** Whether the server announces the capability `name`. */
  const can = (name) => capabilities.value[name] === true

  useAuth().onLogin(load)

  return { capabilities, can, load }
})
