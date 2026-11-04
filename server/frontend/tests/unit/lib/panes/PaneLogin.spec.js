import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import PrimeVue from 'primevue/config'

// The sign-in form with the site's Turnstile check: the widget renders only on
// a site that has it on, the form waits for its token, sends it, and spends
// it on every attempt. Cloudflare's script is replaced by a fake `turnstile`
// API on window, which the loader takes as already loaded.

const mocks = vi.hoisted(() => ({ auth: null }))
vi.mock('@/stores/auth', () => ({ useAuth: () => mocks.auth }))

import PaneLogin from '@/lib/panes/PaneLogin.vue'
import { resetTurnstileLoader, TURNSTILE_ACTION } from '@/lib/turnstile'

const fakeTurnstile = () => {
  const api = {
    options: null,
    render: vi.fn((el, options) => {
      api.options = options
      return 'widget-1'
    }),
    reset: vi.fn(),
    remove: vi.fn(),
    solve: (token) => api.options.callback(token),
    expire: () => api.options['expired-callback']()
  }
  return api
}

const makeAuth = (siteKey) =>
  reactive({
    mfaPending: false,
    turnstileSiteKey: siteKey,
    loadLoginOptions: vi.fn(async () => {}),
    login: vi.fn(async () => {})
  })

const mountPane = async () => {
  const wrapper = mount(PaneLogin, {
    global: { plugins: [PrimeVue] },
    attachTo: document.body
  })
  await flushPromises()
  await wrapper.get('#login-email').setValue('someone@example.com')
  await wrapper.get('#login-password input').setValue('a password')
  return wrapper
}

const loginButton = (wrapper) => wrapper.findAll('button').find((b) => b.text().includes('Login'))

describe('PaneLogin with Turnstile', () => {
  let turnstile

  beforeEach(() => {
    resetTurnstileLoader()
    turnstile = fakeTurnstile()
    window.turnstile = turnstile
  })

  afterEach(() => {
    delete window.turnstile
    document.body.innerHTML = ''
  })

  it('reads the login options when it mounts', async () => {
    mocks.auth = makeAuth(null)
    await mountPane()
    expect(mocks.auth.loadLoginOptions).toHaveBeenCalled()
  })

  it('renders no widget and signs in without a token when Turnstile is off', async () => {
    mocks.auth = makeAuth(null)
    const wrapper = await mountPane()

    expect(wrapper.find('[data-testid="turnstile"]').exists()).toBe(false)
    expect(turnstile.render).not.toHaveBeenCalled()
    expect(loginButton(wrapper).attributes('disabled')).toBeUndefined()

    await loginButton(wrapper).trigger('click')
    expect(mocks.auth.login).toHaveBeenCalledWith({
      email: 'someone@example.com',
      password: 'a password',
      turnstileToken: null
    })
  })

  it('renders the widget for the site key and the login action', async () => {
    mocks.auth = makeAuth('site-key-1')
    await mountPane()

    expect(turnstile.render).toHaveBeenCalledTimes(1)
    expect(turnstile.options.sitekey).toBe('site-key-1')
    expect(turnstile.options.action).toBe(TURNSTILE_ACTION)
  })

  it('waits for the token before the form can be sent', async () => {
    mocks.auth = makeAuth('site-key-1')
    const wrapper = await mountPane()

    expect(loginButton(wrapper).attributes('disabled')).toBeDefined()
    await wrapper.get('#login-password input').trigger('keyup', { key: 'Enter' })
    expect(mocks.auth.login).not.toHaveBeenCalled()

    turnstile.solve('token-1')
    await flushPromises()
    expect(loginButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('sends the token and spends it on the attempt', async () => {
    mocks.auth = makeAuth('site-key-1')
    const wrapper = await mountPane()
    turnstile.solve('token-1')
    await flushPromises()

    await loginButton(wrapper).trigger('click')
    await flushPromises()

    expect(mocks.auth.login).toHaveBeenCalledWith(
      expect.objectContaining({ turnstileToken: 'token-1' })
    )
    expect(turnstile.reset).toHaveBeenCalledWith('widget-1')
    // Spent: the form waits for a fresh token before it can be sent again.
    expect(loginButton(wrapper).attributes('disabled')).toBeDefined()
  })

  it('spends the token on a refused attempt too', async () => {
    mocks.auth = makeAuth('site-key-1')
    mocks.auth.login = vi.fn(async () => {
      throw new Error('HTTP 400')
    })
    const wrapper = await mountPane()
    turnstile.solve('token-1')
    await flushPromises()

    await loginButton(wrapper).trigger('click')
    await flushPromises()

    expect(turnstile.reset).toHaveBeenCalledWith('widget-1')
    expect(loginButton(wrapper).attributes('disabled')).toBeDefined()
  })

  it('spends the token on a sign-in that goes on to the code step', async () => {
    mocks.auth = makeAuth('site-key-1')
    // The password step answers that a code is needed: the code step replaces
    // the form, and its widget, before the attempt finishes.
    mocks.auth.login = vi.fn(async () => {
      mocks.auth.mfaPending = true
      await flushPromises()
    })
    const wrapper = await mountPane()
    turnstile.solve('token-1')
    await flushPromises()

    await loginButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.find('#login-mfa-code').exists()).toBe(true)

    // Cancel: the form comes back with a fresh widget, still unsolved.
    mocks.auth.mfaPending = false
    await flushPromises()
    await wrapper.get('#login-password input').setValue('a password')

    expect(turnstile.render).toHaveBeenCalledTimes(2)
    expect(loginButton(wrapper).attributes('disabled')).toBeDefined()
  })

  it('takes the form back when the token expires', async () => {
    mocks.auth = makeAuth('site-key-1')
    const wrapper = await mountPane()
    turnstile.solve('token-1')
    await flushPromises()
    turnstile.expire()
    await flushPromises()

    expect(loginButton(wrapper).attributes('disabled')).toBeDefined()
  })

  it('removes the widget when the form goes', async () => {
    mocks.auth = makeAuth('site-key-1')
    const wrapper = await mountPane()
    wrapper.unmount()
    expect(turnstile.remove).toHaveBeenCalledWith('widget-1')
  })
})
