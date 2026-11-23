import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import PrimeVue from 'primevue/config'

import BaseTabbedPanel from '@/lib/base/BaseTabbedPanel.vue'

// The barrel pulls in the runtime config; the panel needs only these three.
vi.mock('@/lib/base', () => ({
  BaseBreadcrumb: { template: '<nav />' },
  BaseStatusIcon: { template: '<span />' },
  BaseLoadError: { template: '<div class="load-error">failed</div>' }
}))

// The panel every browser table sits in. A reload must not unmount the table:
// that dropped the focused row (focus fell to <body>, so an Enter reached no
// table) and the scroll position. The spinner is drawn over the content once
// the content has been shown, and stands in its place only on the first load.

// The real PrimeVue Panel renders the body, so the pass-through `content`
// options (aria-busy) land on the element the app has.
const Content = defineComponent({
  name: 'PanelContent',
  setup: () => () =>
    h('div', { class: 'content', 'data-testid': 'content' }, [
      h('button', { class: 'row', 'data-testid': 'row' }, 'row 0'),
      h('p', { class: 'empty' }, 'No samples')
    ])
})

const mountPanel = (props = {}) =>
  mount(BaseTabbedPanel, {
    props: { label: 'Ions', ...props },
    slots: { default: () => h(Content) },
    attachTo: document.body,
    global: {
      plugins: [PrimeVue],
      stubs: {
        ProgressSpinner: { template: '<span class="progress" />' }
      }
    }
  })

const body = (wrapper) => wrapper.find('.p-panel-content')

describe('BaseTabbedPanel', () => {
  let wrapper

  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    vi.useRealTimers()
  })

  it('keeps the content mounted as the same node through a reload', async () => {
    wrapper = mountPanel()
    const before = wrapper.find('[data-testid="content"]').element

    await wrapper.setProps({ loading: true })
    vi.advanceTimersByTime(1000)
    await nextTick()
    const during = wrapper.find('[data-testid="content"]')
    expect(during.exists()).toBe(true)
    expect(during.element).toBe(before)
    expect(wrapper.find('.spinner').exists()).toBe(true)

    await wrapper.setProps({ loading: false })
    expect(wrapper.find('[data-testid="content"]').element).toBe(before)
  })

  it('leaves focus on the focused row during and after a reload', async () => {
    wrapper = mountPanel()
    const row = wrapper.find('[data-testid="row"]').element
    row.focus()
    expect(document.activeElement).toBe(row)

    await wrapper.setProps({ loading: true })
    vi.advanceTimersByTime(1000)
    await nextTick()
    expect(wrapper.find('.spinner').exists()).toBe(true)
    expect(document.activeElement).toBe(row)

    await wrapper.setProps({ loading: false })
    expect(document.activeElement).toBe(row)
  })

  it('keeps the content scrolled where it was', async () => {
    wrapper = mountPanel()
    const content = wrapper.find('[data-testid="content"]').element
    content.scrollTop = 120

    await wrapper.setProps({ loading: true })
    vi.advanceTimersByTime(1000)
    await nextTick()
    await wrapper.setProps({ loading: false })

    expect(wrapper.find('[data-testid="content"]').element.scrollTop).toBe(120)
  })

  it('shows the spinner over the content and marks the body busy from 300 ms', async () => {
    wrapper = mountPanel()
    await wrapper.setProps({ loading: true })

    vi.advanceTimersByTime(299)
    await nextTick()
    expect(wrapper.find('.spinner').exists()).toBe(false)
    expect(body(wrapper).attributes('aria-busy')).toBeUndefined()

    vi.advanceTimersByTime(1)
    await nextTick()
    const spinner = wrapper.find('.spinner')
    expect(spinner.exists()).toBe(true)
    // Over the content, inside the same body: the class the scoped style
    // lifts above the rows, so a click lands on it and not on a row.
    expect(spinner.classes()).toContain('overlay')
    expect(body(wrapper).attributes('aria-busy')).toBe('true')

    await wrapper.setProps({ loading: false })
    expect(wrapper.find('.spinner').exists()).toBe(false)
    expect(body(wrapper).attributes('aria-busy')).toBeUndefined()
  })

  it('shows neither spinner nor busy for a load shorter than 300 ms', async () => {
    wrapper = mountPanel()
    await wrapper.setProps({ loading: true })
    vi.advanceTimersByTime(200)
    await wrapper.setProps({ loading: false })
    vi.advanceTimersByTime(500)
    await nextTick()

    expect(wrapper.find('.spinner').exists()).toBe(false)
    expect(body(wrapper).attributes('aria-busy')).toBeUndefined()
  })

  it('stands the spinner where the content will be on the first load', async () => {
    wrapper = mountPanel({ loading: true })
    vi.advanceTimersByTime(300)
    await nextTick()

    const spinner = wrapper.find('.spinner')
    expect(spinner.exists()).toBe(true)
    expect(spinner.classes()).not.toContain('overlay')
    // No empty-state text under it.
    expect(wrapper.find('[data-testid="content"]').exists()).toBe(false)
    expect(body(wrapper).attributes('aria-busy')).toBe('true')

    await wrapper.setProps({ loading: false })
    expect(wrapper.find('.spinner').exists()).toBe(false)
    expect(wrapper.find('[data-testid="content"]').exists()).toBe(true)
  })

  it('puts an error in place of the content', async () => {
    wrapper = mountPanel()
    await wrapper.setProps({ error: new Error('boom') })

    expect(wrapper.find('.load-error').exists()).toBe(true)
    expect(wrapper.find('[data-testid="content"]').exists()).toBe(false)
  })

  it('reloads after an error as a first load, then shows the content', async () => {
    // Shown once, then failed: the error took the content's place, so the
    // retry has no content to draw over.
    wrapper = mountPanel()
    await wrapper.setProps({ error: new Error('boom') })
    await wrapper.setProps({ loading: true })
    vi.advanceTimersByTime(300)
    await nextTick()

    const spinner = wrapper.find('.spinner')
    expect(spinner.exists()).toBe(true)
    expect(spinner.classes()).not.toContain('overlay')
    expect(wrapper.find('.load-error').exists()).toBe(false)

    await wrapper.setProps({ loading: false, error: null })
    expect(wrapper.find('[data-testid="content"]').exists()).toBe(true)
  })

  it('keeps the caller pass-through options beside aria-busy', async () => {
    wrapper = mountPanel({
      pt: { root: { id: 'help-root' }, content: { 'data-help': 'body' } }
    })
    await wrapper.setProps({ loading: true })
    vi.advanceTimersByTime(300)
    await nextTick()

    expect(wrapper.find('#help-root').exists()).toBe(true)
    expect(body(wrapper).attributes('data-help')).toBe('body')
    expect(body(wrapper).attributes('aria-busy')).toBe('true')
  })
})
