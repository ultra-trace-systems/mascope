import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// The About dialog is where a support request starts - "which build are you
// on?" - so what it must get right is reporting both builds, noticing when they
// differ (and only then), and putting them on the clipboard. The legal
// documents render in place, since the app is air-gapped by design, so opening
// one must read it from the right place: the web app's own /legal/ files, and
// the server's attributions from the API.

vi.mock('@/api', () => ({ api: { http: { get: vi.fn() } } }))
vi.mock('@/lib/runtime', () => ({ runtime: { meta: {}, version: 'v1.7.3' } }))

import { api } from '@/api'
import DialogAbout from '@/lib/dialogs/DialogAbout.vue'

// Renders its content only while visible, like the real one, and names itself
// by its header so the two dialogs can be told apart.
const DialogStub = {
  name: 'Dialog',
  props: ['visible', 'header'],
  template: '<section v-if="visible" :aria-label="header"><slot /></section>'
}
// `text` is declared so it cannot fall through to the <a> as an attribute: Vue
// would set it as the anchor's DOM `text` property, which replaces its content.
const ButtonStub = {
  props: ['label', 'as', 'href', 'text'],
  template: `<a v-if="as === 'a'" :href="href">{{ label }}</a><button v-else>{{ label }}</button>`
}

const serve = ({ version = 'v1.7.3', notices = 'numpy 2.0.0\nLicense: BSD-3-Clause' } = {}) =>
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

const mountDialog = async () => {
  const wrapper = mount(DialogAbout, {
    props: { visible: true },
    global: {
      stubs: {
        Dialog: DialogStub,
        Button: ButtonStub,
        Message: { template: '<div role="alert"><slot /></div>' },
        ProgressSpinner: { template: '<span>Loading</span>' }
      }
    }
  })
  await flushPromises()
  return wrapper
}

const click = async (wrapper, label) => {
  const button = wrapper.findAll('button').find((found) => found.text() === label)
  expect(button, `no "${label}" button on screen`).toBeTruthy()
  await button.trigger('click')
  await flushPromises()
}

// The version cells as rendered: [web app, server].
const versions = (wrapper) => wrapper.findAll('dd').map((cell) => cell.text())

let tag

beforeEach(() => {
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
})

describe('DialogAbout: versions', () => {
  it('shows the build of both halves, and no warning when they agree', async () => {
    serve()
    const wrapper = await mountDialog()

    expect(versions(wrapper)).toEqual(['v1.7.3', 'v1.7.3'])
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    // Rendered inline rather than raised as an error toast.
    expect(api.http.get).toHaveBeenCalledWith(
      '/version',
      expect.objectContaining({ use: 'read', errors: 'inline' })
    )
  })

  it('warns when the server runs a different build than the tab loaded', async () => {
    serve({ version: 'v1.7.4' })
    const wrapper = await mountDialog()

    expect(versions(wrapper)).toEqual(['v1.7.3', 'v1.7.4'])
    expect(wrapper.get('[role="alert"]').text()).toContain('different versions')
  })

  it('does not call a channel name a mismatch', async () => {
    serve({ version: 'latest' })
    const wrapper = await mountDialog()

    expect(versions(wrapper)).toEqual(['v1.7.3', 'latest'])
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('says the server version is unavailable rather than guessing', async () => {
    serve({ version: new Error('network down') })
    const wrapper = await mountDialog()

    expect(versions(wrapper)).toEqual(['v1.7.3', 'unavailable'])
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('copies both builds and the browser for a support request', async () => {
    serve({ version: 'v1.7.4' })
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText }, userAgent: 'TestBrowser/1.0' })
    const wrapper = await mountDialog()

    await click(wrapper, 'Copy version details')

    expect(writeText).toHaveBeenCalledWith(
      'Mascope web app: v1.7.3\nMascope server: v1.7.4\nBrowser: TestBrowser/1.0'
    )
    expect(wrapper.findAll('button').some((button) => button.text() === 'Copied')).toBe(true)
  })
})

describe('DialogAbout: documents', () => {
  it("renders the server's attributions from the API", async () => {
    serve({ notices: 'numpy 2.0.0\nLicense: BSD-3-Clause' })
    const wrapper = await mountDialog()

    await click(wrapper, 'Third-party notices (server)')

    expect(api.http.get).toHaveBeenCalledWith(
      '/version/third-party-notices',
      expect.objectContaining({ responseType: 'text', errors: 'inline' })
    )
    expect(wrapper.get('[aria-label="Third-party notices: server"] pre').text()).toContain(
      'numpy 2.0.0'
    )
  })

  it("renders the web app's attributions from its own baked-in file", async () => {
    serve()
    const fetch = vi.fn(async () => ({ ok: true, text: async () => 'vue 3.5.0\nLicense: MIT' }))
    vi.stubGlobal('fetch', fetch)
    const wrapper = await mountDialog()

    await click(wrapper, 'Third-party notices (web app)')

    expect(fetch).toHaveBeenCalledWith('/legal/THIRD_PARTY_NOTICES.txt', expect.anything())
    expect(wrapper.get('pre').text()).toContain('vue 3.5.0')
  })

  it('explains a server without generated notices instead of showing nothing', async () => {
    serve({ notices: Object.assign(new Error('Not Found'), { response: { status: 404 } }) })
    const wrapper = await mountDialog()

    await click(wrapper, 'Third-party notices (server)')

    expect(wrapper.find('pre').exists()).toBe(false)
    expect(wrapper.text()).toContain('generated when its image is built')
  })
})

describe('DialogAbout: links', () => {
  it('links the docs, support, privacy notice and release notes, and hides unpublished terms', async () => {
    serve()
    const wrapper = await mountDialog()
    const hrefs = Object.fromEntries(
      wrapper.findAll('a').map((link) => [link.text(), link.attributes('href')])
    )

    expect(hrefs['User documentation']).toBe('/docs/')
    expect(hrefs['support@ultratrace.eu']).toBe('mailto:support@ultratrace.eu')
    expect(hrefs['Privacy notice']).toBe('https://ultratrace.eu/mascope/privacy')
    expect(hrefs['Release notes']).toBe(
      'https://github.com/ultra-trace-systems/mascope/releases/tag/v1.7.3'
    )
    expect(hrefs['Security policy']).toBe(
      'https://github.com/ultra-trace-systems/mascope/security/policy'
    )
    expect('Terms of service' in hrefs).toBe(false)
  })
})
