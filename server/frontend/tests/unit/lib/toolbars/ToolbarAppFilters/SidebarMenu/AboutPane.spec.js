import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// The About tab is where a support request starts - "which build are you on?" -
// so it has to name the build, notice the rare server running a different one
// (and only then), and get the details onto the clipboard even where the async
// Clipboard API does not exist. The licence and the notices open in place; the
// notices are one document built from three sources, and a part that is
// missing must not hide the others.

vi.mock('@/api', () => ({ api: { http: { get: vi.fn() } } }))
vi.mock('@/lib/runtime', () => ({ runtime: { meta: {}, version: 'v1.7.3' } }))

import { api } from '@/api'
import AboutPane from '@/lib/toolbars/ToolbarAppFilters/SidebarMenu/AboutPane.vue'
import { useSidebarMenu } from '@/lib/toolbars/ToolbarAppFilters/SidebarMenu/state.js'

// Renders its content only while visible, like the real one, and names itself
// by its header. `maximizable` is declared so a test can see it is not set.
const DialogStub = {
  name: 'Dialog',
  props: ['visible', 'header', 'maximizable'],
  template: '<section v-if="visible" :aria-label="header"><slot /></section>'
}
// `text` is declared so it cannot fall through to the <a> as an attribute: Vue
// would set it as the anchor's DOM `text` property, which replaces its content.
const ButtonStub = {
  props: ['label', 'as', 'href', 'text'],
  template: `<a v-if="as === 'a'" :href="href">{{ label }}</a><button v-else>{{ label }}</button>`
}
const STUBS = {
  Dialog: DialogStub,
  Button: ButtonStub,
  Message: { template: '<div role="alert"><slot /></div>' },
  ProgressSpinner: { template: '<span>Loading</span>' }
}

const FILES = {
  '/legal/NOTICE.txt': 'Mascope\nCopyright 2021-2026 Ultra Trace Systems Oy\n',
  '/legal/LICENSE.txt': 'Apache License\nVersion 2.0\n',
  '/legal/THIRD_PARTY_NOTICES.txt': 'Mascope web app - third-party notices\n\nvue 3.5.42\n'
}
const SERVER_NOTICES = 'Mascope server - third-party notices\n\nnumpy 2.5.2\n'

const serve = ({ version = 'v1.7.3', notices = SERVER_NOTICES } = {}) =>
  api.http.get.mockImplementation(async (url) => {
    if (url === '/version') {
      if (version instanceof Error) throw version
      return { version }
    }
    if (url === '/version/third-party-notices') {
      if (notices instanceof Error) throw notices
      return { data: notices }
    }
    throw new Error(`unexpected request ${url}`)
  })

const serveFiles = (files = FILES) =>
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url) =>
      url in files ? { ok: true, text: async () => files[url] } : { ok: false, status: 404 }
    )
  )

const mountPane = async ({ tab = 'about' } = {}) => {
  const menu = useSidebarMenu()
  menu.open = true
  menu.tab = tab
  const wrapper = mount(AboutPane, { global: { stubs: STUBS }, attachTo: document.body })
  await flushPromises()
  return { wrapper, menu }
}

const click = async (wrapper, label) => {
  const button = wrapper.findAll('button').find((found) => found.text() === label)
  expect(button, `no "${label}" button on screen`).toBeTruthy()
  await button.trigger('click')
  await flushPromises()
}

const hasButton = (wrapper, label) => wrapper.findAll('button').some((b) => b.text() === label)

let tag
const originalExecCommand = document.execCommand

beforeEach(() => {
  setActivePinia(createPinia())
  api.http.get.mockReset()
  // The build this bundle was made as, as scripts/vite-plugin-legal.js writes it.
  tag = document.createElement('meta')
  tag.name = 'mascope-version'
  tag.content = 'v1.7.3'
  document.head.appendChild(tag)
})

afterEach(() => {
  tag.remove()
  vi.unstubAllGlobals()
  document.execCommand = originalExecCommand
  document.body.innerHTML = ''
})

describe('AboutPane: version', () => {
  it('names the build the page was loaded from, once', async () => {
    serve()
    const { wrapper } = await mountPane()

    expect(wrapper.get('.version').text()).toBe('v1.7.3')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    // Read inline rather than raised as an error toast.
    expect(api.http.get).toHaveBeenCalledWith(
      '/version',
      expect.objectContaining({ use: 'read', errors: 'inline' })
    )
  })

  it('warns, naming both builds, when the server runs a different one', async () => {
    serve({ version: 'v1.7.4' })
    const { wrapper } = await mountPane()

    const warning = wrapper.get('[role="alert"]').text()
    expect(warning).toContain('The server is running v1.7.4')
    expect(warning).toContain('loaded from v1.7.3')
  })

  it('does not call a channel name, or an unreadable server, a mismatch', async () => {
    serve({ version: 'latest' })
    expect((await mountPane()).wrapper.find('[role="alert"]').exists()).toBe(false)

    serve({ version: new Error('network down') })
    expect((await mountPane()).wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('asks the server only while the tab is shown', async () => {
    serve()
    const { menu } = await mountPane({ tab: 'workspaces' })
    expect(api.http.get).not.toHaveBeenCalled()

    menu.tab = 'about'
    await flushPromises()

    expect(api.http.get).toHaveBeenCalledTimes(1)
  })
})

describe('AboutPane: copying the version details', () => {
  it('copies both builds and the browser for a support request', async () => {
    serve({ version: 'v1.7.4' })
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText }, userAgent: 'TestBrowser/1.0' })
    const { wrapper } = await mountPane()

    await click(wrapper, 'Copy version details')

    expect(writeText).toHaveBeenCalledWith(
      'Mascope web app: v1.7.3\nMascope server: v1.7.4\nBrowser: TestBrowser/1.0'
    )
    expect(hasButton(wrapper, 'Copied')).toBe(true)
  })

  it('still copies where there is no Clipboard API (plain HTTP)', async () => {
    serve()
    vi.stubGlobal('navigator', { userAgent: 'TestBrowser/1.0' })
    document.execCommand = vi.fn(() => true)
    const { wrapper } = await mountPane()

    await click(wrapper, 'Copy version details')

    expect(document.execCommand).toHaveBeenCalledWith('copy')
    expect(hasButton(wrapper, 'Copied')).toBe(true)
  })

  it('says so when nothing could be copied', async () => {
    serve()
    vi.stubGlobal('navigator', { userAgent: 'TestBrowser/1.0' })
    document.execCommand = vi.fn(() => false)
    const { wrapper } = await mountPane()

    await click(wrapper, 'Copy version details')

    expect(hasButton(wrapper, 'Copy failed')).toBe(true)
  })
})

describe('AboutPane: documents', () => {
  it('shows NOTICE and both third-party lists as one document, in that order', async () => {
    serve()
    serveFiles()
    const { wrapper } = await mountPane()

    await click(wrapper, 'Notices')

    const text = wrapper.get('[aria-label="Notices"] pre').text()
    const order = ['Copyright 2021-2026', 'vue 3.5.42', 'numpy 2.5.2'].map((s) => text.indexOf(s))
    expect(order.every((at) => at >= 0)).toBe(true)
    expect(order).toEqual([...order].sort((a, b) => a - b))
  })

  it('explains a missing list instead of hiding the rest', async () => {
    serve({ notices: Object.assign(new Error('Not Found'), { response: { status: 404 } }) })
    serveFiles()
    const { wrapper } = await mountPane()

    await click(wrapper, 'Notices')

    const text = wrapper.get('pre').text()
    expect(text).toContain('Copyright 2021-2026')
    expect(text).toContain('vue 3.5.42')
    expect(text).toContain('generated when its image is built')
  })

  it('opens the licence without a full-screen button', async () => {
    serve()
    serveFiles()
    const { wrapper } = await mountPane()

    await click(wrapper, 'Apache License 2.0')

    expect(wrapper.get('[aria-label="Apache License 2.0"] pre').text()).toContain('Apache License')
    const dialogs = wrapper.findAllComponents({ name: 'Dialog' })
    expect(dialogs.length).toBeGreaterThan(0)
    expect(dialogs.every((dialog) => !dialog.props('maximizable'))).toBe(true)
  })
})

describe('AboutPane: links', () => {
  it('links ultratrace.eu, the docs, support, privacy, release notes and security', async () => {
    serve()
    const { wrapper } = await mountPane()
    const hrefs = Object.fromEntries(
      wrapper.findAll('a').map((link) => [link.text(), link.attributes('href')])
    )

    expect(hrefs['ultratrace.eu']).toBe('https://ultratrace.eu')
    expect(hrefs['User documentation']).toBe('/docs/')
    expect(hrefs['support@ultratrace.eu']).toBe('mailto:support@ultratrace.eu')
    expect(hrefs['Privacy notice']).toBe('https://ultratrace.eu/mascope/privacy')
    expect(hrefs['Release notes']).toBe(
      'https://github.com/ultra-trace-systems/mascope/releases/tag/v1.7.3'
    )
    expect(hrefs['Security policy']).toBe(
      'https://github.com/ultra-trace-systems/mascope/security/policy'
    )
    // No terms of service are published yet.
    expect('Terms of service' in hrefs).toBe(false)
  })
})
