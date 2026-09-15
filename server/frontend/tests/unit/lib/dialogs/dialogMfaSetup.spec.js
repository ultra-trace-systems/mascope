import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, ref, nextTick } from 'vue'

import PrimeVue from 'primevue/config'
import RealDialog from 'primevue/dialog'

// The enrolment dialog hands the recovery codes over exactly once. PrimeVue's
// Dialog binds a document keydown listener whenever `closeOnEscape` is set and
// closes on Escape without ever consulting `closable`, and this dialog's
// visibility watcher calls reset() on close - which drops the codes. Enrolment
// is already committed server-side by then, so the codes step has to switch
// both props off, not just the one that hides the X.

vi.mock('@/api', () => ({
  api: { http: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }
}))

vi.mock('@/stores', () => ({
  useApp: () => ({ auth: { user: { email: 'chemist@example.org' } } })
}))

// The copyable field drags in the whole base barrel; the seed it renders is not
// what these tests are about.
vi.mock('@/lib/base', () => ({
  BaseCopyableField: { props: ['field'], template: '<span>{{ field }}</span>' }
}))

// A real QR render wants a canvas, and the dialog only ever puts the data URL
// into an <img src>.
vi.mock('qrcode', () => ({
  default: { toDataURL: vi.fn().mockResolvedValue('data:image/png;base64,AAAA') }
}))

import { api } from '@/api'
import DialogMfaSetup from '@/lib/dialogs/DialogMfaSetup.vue'

const RECOVERY_CODES = ['AAAA1111BBBB2222', 'CCCC3333DDDD4444']

// A Dialog stub that keeps the props under test observable, plus plain form
// controls so the flow can be driven without installing the PrimeVue plugin.
const DialogStub = {
  name: 'Dialog',
  props: ['visible', 'closable', 'closeOnEscape', 'modal', 'header'],
  template: '<div class="dialog"><slot /><slot name="footer" /></div>'
}

const mountDialog = () =>
  mount(DialogMfaSetup, {
    props: { visible: true },
    global: {
      stubs: {
        Dialog: DialogStub,
        // The label is the only handle the footer buttons offer; @click lands as
        // a fall-through native listener on the root, as it does on the real one.
        Button: { props: ['label'], template: '<button>{{ label }}</button>' },
        InputText: {
          props: ['modelValue'],
          emits: ['update:modelValue'],
          template:
            '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />'
        },
        FloatLabel: { template: '<div><slot /></div>' },
        Message: { template: '<div><slot /></div>' }
      }
    }
  })

const clickButton = async (wrapper, label) => {
  const button = wrapper.findAll('button').find((b) => b.text() === label)
  expect(button, `no "${label}" button on screen`).toBeTruthy()
  await button.trigger('click')
  await flushPromises()
}

// scan -> confirm -> codes, the way a user walks it.
const enrolToCodesStep = async () => {
  api.http.post
    .mockResolvedValueOnce({ secret: 'JBSWY3DPEHPK3PXP', provisioning_uri: 'otpauth://totp/x' })
    .mockResolvedValueOnce({ recovery_codes: RECOVERY_CODES })

  const wrapper = mountDialog()
  await clickButton(wrapper, 'Set up')
  await wrapper.find('#mfa-confirm-code').setValue('123456')
  await clickButton(wrapper, 'Verify and turn on')
  return wrapper
}

describe('DialogMfaSetup recovery-codes step', () => {
  beforeEach(() => {
    api.http.post.mockReset()
    api.http.get.mockReset()
    api.http.get.mockResolvedValue({ enabled: true, available: true, required: false })
  })

  it('leaves Escape working on the steps before the codes', () => {
    const dialog = mountDialog().findComponent(DialogStub)

    expect(dialog.props('closable')).toBe(true)
    expect(dialog.props('closeOnEscape')).toBe(true)
  })

  it('blocks Escape, not just the X, once the codes are on screen', async () => {
    const wrapper = await enrolToCodesStep()
    expect(wrapper.text()).toContain(RECOVERY_CODES[0])

    const dialog = wrapper.findComponent(DialogStub)
    expect(dialog.props('closable')).toBe(false)
    // The regression this guards: `closable` does not gate PrimeVue's Escape
    // handler, so without this the codes can be dismissed into nothing.
    expect(dialog.props('closeOnEscape')).toBe(false)
  })

  it('documents why: any close destroys the codes (passes pre-fix, not a guard)', async () => {
    const wrapper = await enrolToCodesStep()
    expect(wrapper.find('#recovery-codes').exists()).toBe(true)

    await wrapper.setProps({ visible: false })
    await flushPromises()

    expect(wrapper.find('#recovery-codes').exists()).toBe(false)
    expect(wrapper.text()).not.toContain(RECOVERY_CODES[0])
  })
})

// Pins the PrimeVue behaviour the fix depends on: the Escape listener is
// bound ONCE at open and never consults `closable`, so flipping
// `closeOnEscape` mid-flight is what actually has to stop the close.
describe('the PrimeVue contract this relies on', () => {
  const Host = defineComponent({
    setup(_, { expose }) {
      const visible = ref(true)
      const allowEscape = ref(true)
      expose({ visible, allowEscape })
      return () =>
        h(RealDialog, {
          visible: visible.value,
          'onUpdate:visible': (v) => (visible.value = v),
          modal: true,
          header: 'x',
          closable: allowEscape.value,
          closeOnEscape: allowEscape.value
        })
    }
  })

  const open = async () => {
    const w = mount(Host, { attachTo: document.body, global: { plugins: [PrimeVue] } })
    await flushPromises()
    // What onEnter does for our purposes; happy-dom skips the transition hook.
    w.findComponent(RealDialog).vm.bindGlobalListeners()
    return w
  }

  const escape = async () => {
    document.dispatchEvent(new KeyboardEvent('keydown', { code: 'Escape', key: 'Escape' }))
    await flushPromises()
    await nextTick()
  }

  it('closes on Escape while closeOnEscape is true', async () => {
    const w = await open()
    await escape()
    expect(w.vm.visible).toBe(false)
  })

  it('ignores Escape once closeOnEscape flips false on an already-open dialog', async () => {
    const w = await open()
    w.vm.allowEscape = false
    await nextTick()
    await escape()
    expect(w.vm.visible).toBe(true)
  })
})
