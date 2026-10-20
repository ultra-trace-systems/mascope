import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive, nextTick } from 'vue'
import PrimeVue from 'primevue/config'
import Tooltip from 'primevue/tooltip'

// The Batch, Sample and Match tabs are disabled until there is something to
// show in them, and each says what that something is: in a tooltip, and as
// its accessible description. PrimeVue turns pointer events off on a disabled
// tab, so the dashboard turns them back on for its tabs, and has to stop the
// click that lets through itself.

const mocks = vi.hoisted(() => ({ app: null }))
vi.mock('@/stores', () => ({ useApp: () => mocks.app }))
vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: false }))

// Hoisted with the mocks that use it: a factory runs before the file body.
const stub = vi.hoisted(() => (name) => ({
  name,
  props: ['active'],
  template: `<div class="${name}" />`
}))
vi.mock('@/lib/panes', () => ({
  PaneProgress: stub('PaneProgress'),
  PaneBrowserSample: stub('PaneBrowserSample'),
  PaneBrowserMatch: stub('PaneBrowserMatch'),
  PaneTabMatch: stub('PaneTabMatch'),
  PaneTabAcquisitions: stub('PaneTabAcquisitions'),
  PaneTabSample: stub('PaneTabSample'),
  PaneTabBatch: stub('PaneTabBatch')
}))
vi.mock('@/lib/toolbars', () => ({ ToolbarAppFilters: stub('ToolbarAppFilters') }))
vi.mock('@/lib/help', () => ({
  HelpButton: stub('HelpButton'),
  HelpPopover: stub('HelpPopover'),
  DocsButton: stub('DocsButton')
}))

import Dashboard from '@/routes/Dashboard.vue'

const BATCH = { sample_batch_id: 'sb-1', sample_batch_name: 'Batch 1' }
const SAMPLE = { sample_item_id: 'si-1', sample_item_name: 'Sample 1' }

// What each tab says enables it.
const REASONS = [
  ['Batch', 'Open a batch with samples'],
  ['Sample', 'Select a sample'],
  ['Match', 'Open a match from the ion table']
]

// Dispatched rather than triggered, so that `defaultPrevented` can be read
// back: whether the guard let the event through is the point.
const dispatch = (element, type, init = {}) => {
  const Event = type === 'keydown' ? KeyboardEvent : MouseEvent
  const event = new Event(type, { bubbles: true, cancelable: true, ...init })
  element.element.dispatchEvent(event)
  return event
}

function makeApp({ batch = null, samples = [], sample = null, ion = null } = {}) {
  return reactive({
    data: {
      batch: { focused: batch },
      sample: { list: samples, focused: sample },
      match: { visualized: { ion } }
    },
    ui: {
      tab: { active: 'raw files' },
      split: { left: 50, right: 50, top: 50, bottom: 50 },
      help: { docUrl: (path = '') => `/docs/${path}`, bottom: () => ({}) }
    }
  })
}

// The layout around the tabs is not under test; the tabs themselves are the
// real PrimeVue components, since what is asserted is how they render.
const passthrough = { template: '<div><slot /></div>' }
const mountDashboard = () =>
  mount(Dashboard, {
    attachTo: document.body,
    global: {
      plugins: [PrimeVue],
      directives: { tooltip: Tooltip },
      stubs: { Splitter: passthrough, SplitterPanel: passthrough, Panel: passthrough }
    }
  })

const tab = (wrapper, label) =>
  wrapper.findAll('[role="tab"]').find((candidate) => candidate.text().includes(label))

// The tooltip opens on a zero-delay timer, into the document body.
const hover = async (element) => {
  await element.trigger('mouseenter')
  await new Promise((resolve) => setTimeout(resolve, 0))
}
const tooltips = () =>
  [...document.body.querySelectorAll('[role="tooltip"]')].map((node) => node.textContent.trim())

let wrapper

afterEach(() => {
  wrapper?.unmount()
  document.body.innerHTML = ''
})

describe('the disabled dashboard tabs', () => {
  beforeEach(() => {
    mocks.app = makeApp()
  })

  it('are disabled until there is something to show', () => {
    wrapper = mountDashboard()

    for (const label of ['Batch', 'Sample', 'Match']) {
      expect(tab(wrapper, label).attributes('data-p-disabled')).toBe('true')
    }
    expect(tab(wrapper, 'Raw files').attributes('data-p-disabled')).toBe('false')
  })

  it('carry a tooltip, which the enabled tab does not', () => {
    wrapper = mountDashboard()

    for (const label of ['Batch', 'Sample', 'Match']) {
      expect(tab(wrapper, label).attributes('data-pd-tooltip')).toBe('true')
    }
    expect(tab(wrapper, 'Raw files').attributes('data-pd-tooltip')).toBeUndefined()
  })

  it.each(REASONS)('say what enables the %s tab when hovered', async (label, reason) => {
    wrapper = mountDashboard()

    await hover(tab(wrapper, label))

    expect(tooltips()).toContain(reason)
  })

  // PrimeVue's base style turns pointer events off on a disabled tab, which
  // would also keep the tooltip from ever opening; the dashboard turns them
  // back on. Rendered as a div rather than a button for the same reason: a
  // disabled button gets no hover in every browser.
  it('keep their pointer events, and are not buttons', () => {
    wrapper = mountDashboard()

    const batch = tab(wrapper, 'Batch')
    expect(batch.element.tagName).toBe('DIV')
    expect(batch.classes()).toContain('p-disabled')
    expect(batch.attributes('disabled')).toBeUndefined()
  })

  // PrimeVue's Tab puts the disabled state on a button only, so on a div the
  // dashboard says it itself - and the reason with it, since the tooltip is
  // for the pointer alone. The reason describes the tab from outside it: text
  // inside a tab joins its accessible name, and would be read twice.
  it('say so to assistive tech, with the reason', () => {
    wrapper = mountDashboard()

    for (const [label, reason] of REASONS) {
      const element = tab(wrapper, label)
      expect(element.attributes('aria-disabled')).toBe('true')
      const description = document.getElementById(element.attributes('aria-describedby'))
      expect(description.textContent.trim()).toBe(reason)
      expect(element.text()).not.toContain(reason)
    }
    expect(tab(wrapper, 'Raw files').attributes('aria-disabled')).toBeUndefined()
    expect(tab(wrapper, 'Raw files').attributes('aria-describedby')).toBeUndefined()
  })

  // With pointer events back on, the click reaches PrimeVue's Tab, which would
  // happily activate it.
  it('do not open when clicked', async () => {
    wrapper = mountDashboard()

    await tab(wrapper, 'Batch').trigger('click')
    await tab(wrapper, 'Batch').find('span').trigger('click')

    expect(mocks.app.ui.tab.active).toBe('raw files')
  })

  it('do not open on Enter or Space, which are stopped before PrimeVue sees them', async () => {
    wrapper = mountDashboard()
    const sample = tab(wrapper, 'Sample')

    expect(dispatch(sample, 'keydown', { code: 'Enter' }).defaultPrevented).toBe(true)
    expect(dispatch(sample, 'keydown', { code: 'NumpadEnter' }).defaultPrevented).toBe(true)
    expect(dispatch(sample, 'keydown', { code: 'Space' }).defaultPrevented).toBe(true)
    await nextTick()

    expect(mocks.app.ui.tab.active).toBe('raw files')
  })

  // A tabindex="-1" div takes focus on mousedown, which is how a pointer would
  // land focus on a disabled tab in the first place.
  it('do not take focus from a mousedown', () => {
    wrapper = mountDashboard()

    expect(dispatch(tab(wrapper, 'Batch'), 'mousedown').defaultPrevented).toBe(true)
    expect(dispatch(tab(wrapper, 'Raw files'), 'mousedown').defaultPrevented).toBe(false)
  })

  // Only the keys that would activate the tab are stopped: a tab can hold
  // focus as it becomes disabled, and the keyboard must still lead out of it.
  it('let every other key through', () => {
    wrapper = mountDashboard()
    const sample = tab(wrapper, 'Sample')

    for (const code of ['Tab', 'Escape']) {
      expect(dispatch(sample, 'keydown', { code }).defaultPrevented).toBe(false)
    }
  })
})

describe('the enabled dashboard tabs', () => {
  beforeEach(() => {
    mocks.app = makeApp({ batch: BATCH, samples: [SAMPLE], sample: SAMPLE, ion: { id: 1 } })
  })

  it('carry no tooltip', async () => {
    wrapper = mountDashboard()

    const batch = tab(wrapper, 'Batch')
    expect(batch.attributes('data-p-disabled')).toBe('false')
    expect(batch.attributes('data-pd-tooltip')).toBeUndefined()

    await hover(batch)
    expect(tooltips()).toEqual([])
  })

  // The guard stops clicks on disabled tabs only; an enabled tab still opens.
  it('open when clicked', async () => {
    wrapper = mountDashboard()

    await tab(wrapper, 'Batch').trigger('click')
    expect(mocks.app.ui.tab.active).toBe('batch')

    await tab(wrapper, 'Sample').find('span').trigger('click')
    expect(mocks.app.ui.tab.active).toBe('sample')
  })

  it('gain their tooltip back when disabled again', async () => {
    wrapper = mountDashboard()

    mocks.app.data.sample.focused = null
    await nextTick()

    const sample = tab(wrapper, 'Sample')
    expect(sample.attributes('data-p-disabled')).toBe('true')
    expect(sample.attributes('aria-disabled')).toBe('true')
    await hover(sample)
    expect(tooltips()).toContain('Select a sample')
  })

  // The visualized ion is cleared while the keyboard is on the Match tab: the
  // tab is disabled under the focus it holds, and Tab must still lead away.
  it('can be left by keyboard after becoming disabled while focused', async () => {
    wrapper = mountDashboard()
    const match = tab(wrapper, 'Match')
    match.element.focus()
    expect(document.activeElement).toBe(match.element)

    mocks.app.data.match.visualized.ion = null
    await nextTick()
    expect(match.attributes('data-p-disabled')).toBe('true')

    expect(dispatch(match, 'keydown', { code: 'Tab' }).defaultPrevented).toBe(false)
    expect(dispatch(match, 'keydown', { code: 'Enter' }).defaultPrevented).toBe(true)
  })
})
