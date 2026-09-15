import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'

// The sign-in screen's legal footer is what someone without an account can
// reach, so it has to link the published documents by default, follow the
// deployment's own settings, and leave out a link that is hidden or unusable.

vi.mock('@/lib/runtime', () => ({ runtime: { meta: {}, version: null } }))

import { runtime } from '@/lib/runtime'
import BaseLegalFooter from '@/lib/base/BaseLegalFooter.vue'

const links = (wrapper) =>
  Object.fromEntries(wrapper.findAll('a').map((link) => [link.text(), link]))

afterEach(() => {
  runtime.meta = {}
})

describe('BaseLegalFooter', () => {
  it('links the published privacy notice and support, and no terms yet', () => {
    const wrapper = mount(BaseLegalFooter)
    const { 'Privacy notice': privacy, Support: support, ...rest } = links(wrapper)

    expect(privacy.attributes('href')).toBe('https://ultratrace.eu/mascope/privacy')
    expect(privacy.attributes('target')).toBe('_blank')
    expect(privacy.attributes('rel')).toContain('noopener')
    // A mail link opens the mail client, not an empty tab.
    expect(support.attributes('href')).toBe('mailto:support@ultratrace.eu')
    expect(support.attributes('target')).toBeUndefined()
    expect(Object.keys(rest)).toEqual([])
    expect(wrapper.text()).toContain('Ultra Trace Systems Oy')
  })

  it("follows the deployment's own links", () => {
    runtime.meta = {
      privacy_notice_url: 'https://example.org/privacy',
      terms_url: 'https://example.org/terms',
      support_url: ''
    }

    const found = links(mount(BaseLegalFooter))

    expect(Object.keys(found)).toEqual(['Privacy notice', 'Terms of service'])
    expect(found['Privacy notice'].attributes('href')).toBe('https://example.org/privacy')
    expect(found['Terms of service'].attributes('href')).toBe('https://example.org/terms')
  })

  it('leaves out a link that is not a web URL', () => {
    runtime.meta = { privacy_notice_url: 'javascript:alert(1)' }

    expect(Object.keys(links(mount(BaseLegalFooter)))).toEqual(['Support'])
  })
})
