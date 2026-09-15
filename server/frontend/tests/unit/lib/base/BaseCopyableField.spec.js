import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'

const { push } = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock('@/stores', () => ({ useApp: () => ({ ui: { notification: { push } } }) }))

const { default: BaseCopyableField } = await import('@/lib/base/BaseCopyableField.vue')

// The copy button beside a value. navigator.clipboard is missing on a page
// served over plain HTTP from a network address, so the copy has to fall back
// to the copy command - from inside the field, since it is used in dialogs and
// drawers whose focus trap would pull focus out of anything on <body>. 'copy'
// is emitted only for a copy that happened: a listener discards a one-time
// value (a generated password) on it, and must not do so unread. A copy that
// did not happen says so, since the button looks the same either way.

const originalExecCommand = document.execCommand

// A plain <button> stands in for PrimeVue's; the @click lands on it as a
// fall-through native listener, the way the real Button carries it.
const mountField = (props = { field: 'Xk4-one-time' }) =>
  mount(BaseCopyableField, {
    props,
    attachTo: document.body,
    global: {
      stubs: { Button: { template: '<button />' } },
      directives: { tooltip: {} }
    }
  })

const copy = async (wrapper) => {
  await wrapper.get('button').trigger('click')
  await flushPromises()
}

enableAutoUnmount(afterEach)

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
  document.execCommand = originalExecCommand
  document.body.innerHTML = ''
})

describe('BaseCopyableField', () => {
  it('copies through the copy command, from inside the field, where there is no Clipboard API', async () => {
    vi.stubGlobal('navigator', {})
    let copied, parent
    document.execCommand = vi.fn(() => {
      const textarea = document.querySelector('textarea')
      copied = textarea?.value
      parent = textarea?.parentElement
      return true
    })
    const wrapper = mountField()

    await copy(wrapper)

    expect(document.execCommand).toHaveBeenCalledWith('copy')
    expect(copied).toBe('Xk4-one-time')
    expect(parent).toBe(wrapper.element)
    expect(wrapper.emitted('copy')).toHaveLength(1)
    expect(push).not.toHaveBeenCalled()
  })

  it('does not report a copy when nothing could copy, and warns instead', async () => {
    vi.stubGlobal('navigator', {})
    document.execCommand = vi.fn(() => false)
    const wrapper = mountField()

    await copy(wrapper)

    expect(wrapper.emitted('copy')).toBeUndefined()
    expect(push).toHaveBeenCalledTimes(1)
    expect(push).toHaveBeenCalledWith(expect.objectContaining({ status: 'warning' }))
  })

  it('uses the Clipboard API where it exists', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    document.execCommand = vi.fn()
    const wrapper = mountField({ field: 42.5 })

    await copy(wrapper)

    expect(writeText).toHaveBeenCalledWith('42.5')
    expect(document.execCommand).not.toHaveBeenCalled()
    expect(wrapper.emitted('copy')).toHaveLength(1)
    expect(push).not.toHaveBeenCalled()
  })
})
