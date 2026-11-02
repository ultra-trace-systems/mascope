// Cloudflare Turnstile, the human check on the sign-in form when the site
// turns it on (server: api/new/auth/turnstile.py).
//
// Cloudflare's script is loaded only on a site that has Turnstile on, and only
// once: the login pane mounts and unmounts as people sign in and out. It is
// loaded in explicit mode, so it renders nothing by itself and the widget
// component decides where and when. nginx's Content-Security-Policy allows the
// script and the widget's frame from challenges.cloudflare.com.

export const TURNSTILE_SCRIPT_URL =
  'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'

// The action the widget is rendered with; the server refuses a token solved
// for any other.
export const TURNSTILE_ACTION = 'login'

let loading = null

/**
 * Load Cloudflare's Turnstile script, once.
 *
 * A failed load is forgotten, so the next mount of the form tries again.
 *
 * @returns {Promise<object>} Cloudflare's `turnstile` API.
 */
export function loadTurnstile() {
  if (window.turnstile) return Promise.resolve(window.turnstile)
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const script = document.createElement('script')
      script.src = TURNSTILE_SCRIPT_URL
      script.async = true
      script.onload = () =>
        window.turnstile
          ? resolve(window.turnstile)
          : reject(new Error('Turnstile loaded without its API'))
      script.onerror = () => reject(new Error('Turnstile could not be loaded'))
      document.head.appendChild(script)
    }).catch((error) => {
      loading = null
      throw error
    })
  }
  return loading
}

/** Forget a load in flight. For tests. */
export function resetTurnstileLoader() {
  loading = null
}
