import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'

import PrimeVue from 'primevue/config'

// The dialog for files the upload refused. It asks for what their names lack
// - an instrument, an ionization mode token - and hands the renamed files back.
// Whether a name needs a token depends on the server, whose answer can arrive
// after the drop: a file refused for want of one is then fine as it is, and
// must still be uploaded rather than dropped.

const mocks = vi.hoisted(() => ({ app: null, server: null }))

vi.mock('@/stores', () => ({ useApp: () => mocks.app }))
vi.mock('@/stores/data/modules/instrument', () => ({
  useInstrument: () => ({ typeOf: (name) => (name === 'Orbi-1' ? 'orbi' : null) })
}))
vi.mock('@/stores/server', () => ({
  TOKENLESS_UPLOADS: 'files_uploads_without_ionization_token'
}))

import DialogFileUpload from '@/lib/dialogs/DialogFileUpload.vue'

const passthrough = (name) => ({
  name,
  props: ['visible', 'label', 'disabled', 'modelValue', 'options'],
  emits: ['click', 'update:modelValue', 'update:visible'],
  template: '<div><slot /><slot name="footer" /></div>'
})
const stubs = Object.fromEntries(
  ['Dialog', 'Button', 'Select', 'MultiSelect', 'FloatLabel', 'Message', 'DialogIonizationOp'].map(
    (name) => [name, passthrough(name)]
  )
)

const file = (name) => ({ id: name, name, type: 'application/octet-stream', data: {} })

const mountDialog = (files) =>
  mount(DialogFileUpload, {
    props: { files, active: true },
    global: { plugins: [PrimeVue], stubs }
  })

const button = (wrapper, label) =>
  wrapper.findAllComponents({ name: 'Button' }).find((b) => b.props('label') === label)

describe('DialogFileUpload', () => {
  beforeEach(() => {
    mocks.server = reactive({ tokenless: false })
    mocks.app = {
      data: {
        ionization: {
          mode: { list: [{ ionization_mode_name: 'Nitrate', ionization_mode_token: 'NO3' }] }
        },
        instrument: { list: [{ instrument: 'Orbi-1', type: 'orbi' }] }
      },
      uppy: { clearInvalid: vi.fn() },
      server: {
        can: (name) => mocks.server.tokenless && name === 'files_uploads_without_ionization_token'
      }
    }
  })

  it('asks for a token while the server needs one', () => {
    const wrapper = mountDialog([file('Orbi-1_a.raw')])

    expect(wrapper.text()).toContain('Files missing ionization mode')
    expect(wrapper.text()).not.toContain('Ready to upload')
  })

  it('uploads a file the server turned out to accept, as it is', async () => {
    const wrapper = mountDialog([file('Orbi-1_a.raw')])

    mocks.server.tokenless = true
    await nextTick()
    expect(wrapper.text()).toContain('Ready to upload')
    await button(wrapper, 'Save').vm.$emit('click')

    const [[uploaded]] = wrapper.emitted('upload')
    expect(uploaded.map(({ name }) => name)).toEqual(['Orbi-1_a.raw'])
    expect(mocks.app.uppy.clearInvalid).toHaveBeenCalled()
  })
})
