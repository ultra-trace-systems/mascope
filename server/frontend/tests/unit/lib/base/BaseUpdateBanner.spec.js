import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PrimeVue from 'primevue/config'

// The banner offers a reload; what it adds is saying what the reload brings -
// the build's name when the new index.html carries one, and a link to what
// changed - without presenting a channel such as `latest` as a version.

vi.mock('@/lib/runtime', () => ({ runtime: { meta: {}, version: null } }))

import BaseUpdateBanner from '@/lib/base/BaseUpdateBanner.vue'
import { useUpdate } from '@/lib/update'

const noop = {}

const mountBanner = (version) => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const update = useUpdate()
  update.available = true
  update.version = version
  return mount(BaseUpdateBanner, {
    global: { plugins: [pinia, PrimeVue], directives: { tooltip: noop, ripple: noop } }
  })
}

describe('BaseUpdateBanner', () => {
  it('names the version it offers and links its release notes', () => {
    const wrapper = mountBanner('v1.8.0')

    expect(wrapper.text()).toContain('Mascope v1.8.0 is available.')
    const notes = wrapper.get('a')
    expect(notes.text()).toBe("What's new")
    expect(notes.attributes('href')).toBe(
      'https://github.com/ultra-trace-systems/mascope/releases/tag/v1.8.0'
    )
    expect(notes.attributes('target')).toBe('_blank')
  })

  it('falls back to the generic notice and the changelog when the build names nothing', () => {
    const wrapper = mountBanner(null)

    expect(wrapper.text()).toContain('A new version of Mascope is available.')
    expect(wrapper.get('a').attributes('href')).toBe(
      'https://github.com/ultra-trace-systems/mascope/blob/master/CHANGELOG.md'
    )
  })

  it('does not present a channel as a version', () => {
    expect(mountBanner('latest').text()).toContain('A new version of Mascope is available.')
  })
})
