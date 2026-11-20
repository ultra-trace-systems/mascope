import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

// The Chemistry settings: the profiles Mascope ships for a regular user, and
// under Advanced the server's own ionization modes and mechanisms. An open
// lands where its owner says - Profiles from the toolbar, Advanced for one
// that sends the user to set up a mode of their own - and reads again what
// each mode has routed, which no event announces.

const mocks = vi.hoisted(() => ({ load: vi.fn(), help: vi.fn() }))

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: { ionization: { mode: { load: mocks.load } } },
    ui: { help: { set: mocks.help, bottom_start: () => ({}), docUrl: (path) => path } }
  })
}))

import DialogIonizationOp from '@/lib/dialogs/DialogIonizationOp.vue'

const slot = (name, props = []) => ({
  name,
  props,
  template: '<div><slot /></div>'
})

const stubs = {
  Dialog: slot('Dialog', ['visible', 'header']),
  Button: true,
  // A Tabs renders every panel; which is shown is its value.
  Tabs: { name: 'Tabs', props: ['value'], template: '<div class="tabs"><slot /></div>' },
  TabList: slot('TabList'),
  Tab: { name: 'Tab', props: ['value'], template: '<button class="tab"><slot /></button>' },
  TabPanels: slot('TabPanels'),
  TabPanel: {
    name: 'TabPanel',
    props: ['value'],
    template: '<section :data-panel="value"><slot /></section>'
  },
  PaneChemistryProfiles: { name: 'PaneChemistryProfiles', template: '<div class="profiles" />' },
  PaneIonizationMode: {
    name: 'PaneIonizationMode',
    template: '<div class="modes" />',
    methods: { resetFields() {} }
  },
  PaneIonizationMechanism: {
    name: 'PaneIonizationMechanism',
    template: '<div class="mechanisms" />',
    methods: { resetFields() {} }
  }
}

const mountDialog = (props = {}) =>
  mount(DialogIonizationOp, { props: { visible: false, ...props }, global: { stubs } })

const outerTab = (wrapper) => wrapper.findAllComponents({ name: 'Tabs' })[0].props('value')
const innerTab = (wrapper) => wrapper.findAllComponents({ name: 'Tabs' })[1].props('value')

describe('DialogIonizationOp', () => {
  beforeEach(() => vi.clearAllMocks())

  it('is the Chemistry settings: the profiles, then Advanced', () => {
    const wrapper = mountDialog()

    expect(wrapper.findComponent({ name: 'Dialog' }).props('header')).toBe('Chemistry')
    const tabs = wrapper.findAll('.tab').map((tab) => tab.text())
    expect(tabs).toEqual(['Profiles', 'Advanced', 'Ionization Modes', 'Ionization Mechanisms'])
    // The modes and the mechanisms are inside Advanced, the profiles not.
    const advanced = wrapper.find('[data-panel="advanced"]')
    expect(advanced.find('.modes').exists()).toBe(true)
    expect(advanced.find('.mechanisms').exists()).toBe(true)
    expect(advanced.find('.profiles').exists()).toBe(false)
    expect(wrapper.find('[data-panel="profiles"] .profiles').exists()).toBe(true)
  })

  it('opens on the profiles by default', async () => {
    const wrapper = mountDialog()
    await wrapper.setProps({ visible: true })

    expect(outerTab(wrapper)).toBe('profiles')
    expect(innerTab(wrapper)).toBe('modes')
  })

  it('opens on Advanced when its owner says so, at the modes', async () => {
    const wrapper = mountDialog({ start: 'advanced' })
    await wrapper.setProps({ visible: true })

    expect(outerTab(wrapper)).toBe('advanced')
    expect(innerTab(wrapper)).toBe('modes')
  })

  it('goes back to where its owner says at every open', async () => {
    const wrapper = mountDialog()
    await wrapper.setProps({ visible: true })
    await wrapper.findAllComponents({ name: 'Tabs' })[0].vm.$emit('update:value', 'advanced')
    await wrapper.findAllComponents({ name: 'Tabs' })[1].vm.$emit('update:value', 'mechanisms')
    expect(outerTab(wrapper)).toBe('advanced')

    await wrapper.setProps({ visible: false })
    await wrapper.setProps({ visible: true })

    expect(outerTab(wrapper)).toBe('profiles')
    expect(innerTab(wrapper)).toBe('modes')
  })

  it('reads the modes again at each open, for what they have routed', async () => {
    const wrapper = mountDialog()
    expect(mocks.load).not.toHaveBeenCalled()

    await wrapper.setProps({ visible: true })
    expect(mocks.load).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ visible: false })
    expect(mocks.load).toHaveBeenCalledTimes(1)
  })
})
